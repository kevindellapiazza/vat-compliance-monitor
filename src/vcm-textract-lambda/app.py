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

# === Load VAT rates from CSV stored in S3 ===
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

# === Helper to extract a field with regex ===
def extract_field(pattern, text):
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None

# === Normalize numbers with locale awareness ===
def normalize_number(value: str):
    if not value:
        return None
    v = value.strip().replace('€','').replace('$','').replace('CHF','')
    # If both separators exist, assume EU style: “1.234,56”
    if '.' in v and ',' in v:
        v = v.replace('.', '').replace(',', '.')
    else:
        v = v.replace(',', '')  # US style “1,234.56”
    try:
        return float(v)
    except:
        return None

# === Send message to Slack ===
def send_slack_notification(message):
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        http = urllib3.PoolManager()
        payload = {"text": message}
        http.request("POST", webhook, body=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})

# === Write Parquet file to S3 ===
def save_parquet_to_s3(data: dict, key: str):
    df = pd.DataFrame([data])
    table_arrow = pa.Table.from_pandas(df)
    tmp = f"/tmp/{key}.parquet"
    pq.write_table(table_arrow, tmp)
    with open(tmp, 'rb') as f:
        s3.upload_fileobj(f, PARQUET_OUTPUT_BUCKET, f"{PARQUET_OUTPUT_PREFIX}{key}.parquet")

# === Lambda Handler ===
def lambda_handler(event, context):
    # 1. Get S3 event info
    bucket = event['Records'][0]['s3']['bucket']['name']
    key    = event['Records'][0]['s3']['object']['key']
    invoice_id = os.path.basename(key).replace('.pdf','')

    # 2. OCR with Textract
    tx = boto3.client('textract')
    resp = tx.detect_document_text(Document={'S3Object':{'Bucket':bucket,'Name':key}})
    lines = [b['Text'] for b in resp['Blocks'] if b['BlockType']=='LINE']
    full_text = '\n'.join(lines)

    # 3. Extract fields with more flexible patterns
    vat_id_raw      = extract_field(r'Supplier\s+VAT\s*ID[:\-]?\s*(\w+)', full_text)
    vat_rate_str    = extract_field(r'(?:VAT|IVA|Sales\s*Tax)\s*\(?\s*([\d.,]+)\s*%?\s*\)?', full_text)
    vat_amount_str  = extract_field(r'(?:VAT|IVA|Sales\s*Tax)\s*\([\d.,]+%\)\s*([\u20AC\d.,$£CHF]+)', full_text)
    net_total_str   = extract_field(r'(?:Subtotal|Net\s*Total|Amount\s*Due)\s*[:\-]?\s*([\u20AC\d.,$£CHF]+)', full_text)

    # 4. Normalize numbers
    raw_rate = float(vat_rate_str.replace(',','.')) if vat_rate_str else None
    vat_rate  = raw_rate if raw_rate and raw_rate <= 1 else (raw_rate/100 if raw_rate else None)
    vat_amount = normalize_number(vat_amount_str)
    net_total  = normalize_number(net_total_str)

    # 5. Load allowed VAT rates
    allowed = load_allowed_rates()

    # 6. Run validations
    reasons = []
    status = "PASS"

    # A) Required fields
    if not (vat_id_raw and vat_rate is not None and vat_amount is not None):
        reasons.append("Missing one or more required fields")
        status = "FAIL"

    # B) Country detection
    vat_id_clean = (vat_id_raw or "").replace(' ','').upper()
    m = re.match(r'^([A-Z]{2})\d+', vat_id_clean)
    country = m.group(1) if m else None

    # C) VAT rate valid?
    if not country or country not in allowed or vat_rate not in allowed.get(country, []):
        reasons.append(f"Invalid VAT rate {vat_rate} for {country}")
        status = "FAIL"

    # D) Math check
    if net_total and vat_rate and vat_amount is not None:
        expected = round(net_total * vat_rate, 2)
        if abs(expected - vat_amount) > 0.5:
            reasons.append(f"Math check failed: expected {expected}, got {vat_amount}")
            status = "FAIL"

    # 7. Build result
    result = {
        'invoice_id': invoice_id,
        'country': country or "N/A",
        'vat_rate': vat_rate,
        'vat_amount': vat_amount,
        'supplier_vat_id': vat_id_clean or "N/A",
        'status': status,
        'reason': "; ".join(reasons) or "All checks passed",
        'ocr_text': full_text,
        'timestamp': datetime.datetime.utcnow().isoformat()
    }

    # 8. Persist
    table.put_item(Item={k: (Decimal(str(v)) if isinstance(v,float) else v) for k,v in result.items()})
    save_parquet_to_s3(result, invoice_id)

    # 9. Notify
    print("Validation result:", status, reasons)
    send_slack_notification(
        f"✅ Invoice {invoice_id} | Country: {country} | Status: {status} | Reason: {result['reason']}"
    )

    return {'statusCode':200,'body':json.dumps('Validation complete')}
