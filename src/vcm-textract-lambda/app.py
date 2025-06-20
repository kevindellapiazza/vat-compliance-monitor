# Copyright © Kevin Della Piazza
# For educational and portfolio use only.
# Do not reuse, copy, or publish without permission.

import boto3
import csv
import json
import re
import datetime
import os
import urllib3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from decimal import Decimal

# === AWS Clients ===
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('vcm-invoice-status')

# === CONFIG ===
CONFIG_BUCKET = 'vcm-config-kevin'
CONFIG_FILE = 'config/allowed-vat-rates.csv'
PARQUET_OUTPUT_BUCKET = 'vcm-config-kevin'
PARQUET_OUTPUT_PREFIX = 'data/athena_output/'

# === Country Fix Map (fuzzy OCR correction) ===
FUZZY_COUNTRY_FIX = {
    '1T': 'IT', 'lt': 'LT', 'de': 'DE', 'fr': 'FR', 'es': 'ES',
    'ch': 'CH', 'be': 'BE', 'nl': 'NL'
}

def load_allowed_rates():
    resp = s3.get_object(Bucket=CONFIG_BUCKET, Key=CONFIG_FILE)
    lines = resp['Body'].read().decode('utf-8').splitlines()
    reader = csv.DictReader(lines)
    rates = {}
    for row in reader:
        country = row['country'].upper()
        rate = float(row['rate'])
        rates.setdefault(country, []).append(rate)
    return rates

def extract_field(pattern, text):
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return m.group(1).strip() if m else None

def normalize_number(value: str):
    if not value:
        return None, None
    value = value.strip()
    match = re.match(r'^([€$£₣]|CHF)?\s*([0-9.,]+)', value)
    if not match:
        return None, None
    symbol, number = match.groups()
    currency = {
        '€': 'EUR', '$': 'USD', '£': 'GBP', '₣': 'CHF', 'CHF': 'CHF'
    }.get(symbol, 'UNKNOWN')
    if '.' in number and ',' in number:
        number = number.replace('.', '').replace(',', '.')
    else:
        number = number.replace(',', '')
    try:
        return float(number), currency
    except:
        return None, None

def extract_key_value_map(blocks):
    key_map = {}
    block_map = {b['Id']: b for b in blocks}
    for b in blocks:
        if b['BlockType'] == 'KEY_VALUE_SET' and 'KEY' in b.get('EntityTypes', []):
            key_text, val_text = '', ''
            for rel in b.get('Relationships', []):
                if rel['Type'] == 'CHILD':
                    key_text = ' '.join(block_map[cid]['Text'] for cid in rel['Ids'] if block_map[cid]['BlockType'] == 'WORD')
                if rel['Type'] == 'VALUE':
                    val_block = block_map.get(rel['Ids'][0])
                    for val_rel in val_block.get('Relationships', []):
                        if val_rel['Type'] == 'CHILD':
                            val_text = ' '.join(block_map[cid]['Text'] for cid in val_rel['Ids'] if block_map[cid]['BlockType'] == 'WORD')
            if key_text and val_text:
                key_map[key_text.strip().lower()] = val_text.strip()
    return key_map

def send_slack_notification(msg):
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if hook:
        http = urllib3.PoolManager()
        http.request("POST", hook,
                     body=json.dumps({"text": msg}).encode(),
                     headers={"Content-Type": "application/json"})

def save_parquet_to_s3(data: dict, key: str):
    df = pd.DataFrame([data])
    tbl = pa.Table.from_pandas(df)
    tmp = f"/tmp/{key}.parquet"
    pq.write_table(tbl, tmp)
    with open(tmp, 'rb') as f:
        s3.upload_fileobj(f, PARQUET_OUTPUT_BUCKET, f"{PARQUET_OUTPUT_PREFIX}{key}.parquet")

def lambda_handler(event, context):
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    invoice_id = os.path.basename(key).replace('.pdf', '')

    tx = boto3.client('textract')
    resp = tx.analyze_document(
        Document={'S3Object': {'Bucket': bucket, 'Name': key}},
        FeatureTypes=["FORMS", "TABLES"]
    )
    blocks = resp['Blocks']
    lines = [b['Text'] for b in blocks if b['BlockType'] == 'LINE']
    full_text = '\n'.join(lines)
    kv_map = extract_key_value_map(blocks)

    # Extract VAT fields
    vat_id_raw = extract_field(
        r'(?:VAT\s*(?:ID|No(?:\.|)|Number|Registration))\s*[:\-]?\s*([A-Za-z0-9\-\s]+)',
        full_text
    ) or kv_map.get("vat id") or kv_map.get("vat number") or kv_map.get("vat registration")

    vat_rate_str = extract_field(r'(?:VAT|IVA|Sales\s*Tax)\s*\(?\s*([\d.,]+)\s*%?\s*\)?', full_text) or kv_map.get("vat rate")
    vat_amount_str = extract_field(r'(?:VAT|IVA|Sales\s*Tax)\s*\([\d.,]+%\)\s*([\u20AC\d.,$£₣CHF]+)', full_text) or kv_map.get("vat amount")
    net_total_str = extract_field(r'(?:Subtotal|Net\s*Total|Amount\s*Due)\s*[:\-]?\s*([\u20AC\d.,$£₣CHF]+)', full_text) or kv_map.get("net total")

    raw_rate = float(vat_rate_str.replace(',', '.')) if vat_rate_str else None
    vat_rate = raw_rate if raw_rate and raw_rate <= 1 else (raw_rate / 100 if raw_rate else None)
    vat_amount, currency = normalize_number(vat_amount_str)
    net_total, _ = normalize_number(net_total_str)

    # Clean VAT ID and country code
    vid = (vat_id_raw or "").upper().replace(" ", "").replace("-", "")
    country = re.match(r'^([A-Z]{2})\d+', vid)
    country = country.group(1) if country else None

    # Fix fuzzy country codes
    if country and country.upper() in FUZZY_COUNTRY_FIX:
        country = FUZZY_COUNTRY_FIX[country.upper()]
    elif country:
        country = country.upper()

    # Validation
    allowed = load_allowed_rates()
    reasons, status = [], "PASS"

    if not (vat_id_raw and vat_rate is not None and vat_amount is not None):
        reasons.append("Missing one or more required fields")
        status = "FAIL"

    if not country or country not in allowed or vat_rate not in allowed[country]:
        reasons.append(f"Invalid VAT rate {vat_rate} for {country}")
        status = "FAIL"

    if net_total and vat_rate and vat_amount is not None:
        expected = round(net_total * vat_rate, 2)
        if abs(expected - vat_amount) > 0.02:
            reasons.append(f"Math check failed: expected {expected}, got {vat_amount}")
            status = "FAIL"

    result = {
        'invoice_id': invoice_id,
        'country': country or "N/A",
        'vat_rate': vat_rate,
        'vat_amount': vat_amount,
        'supplier_vat_id': vid or "N/A",
        'currency': currency or "N/A",
        'status': status,
        'reason': "; ".join(reasons) or "All checks passed",
        'ocr_text': full_text,
        'timestamp': datetime.datetime.utcnow().isoformat()
    }

    table.put_item(Item={k: (Decimal(str(v)) if isinstance(v, float) else v) for k, v in result.items()})
    save_parquet_to_s3(result, invoice_id)
    send_slack_notification(f"Invoice {invoice_id} | Country: {country} | Status: {status} | Reason: {result['reason']}")
    return {'statusCode': 200, 'body': json.dumps('Validation complete')}
