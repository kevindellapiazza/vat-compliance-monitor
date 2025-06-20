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
        return None
    v = value.strip().replace('€','').replace('$','')
    if '.' in v and ',' in v:
        v = v.replace('.', '').replace(',', '.')
    else:
        v = v.replace(',', '')
    try:
        return float(v)
    except:
        return None

def send_slack_notification(msg):
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if hook:
        http = urllib3.PoolManager()
        http.request(
            "POST", hook,
            body=json.dumps({"text": msg}).encode(),
            headers={"Content-Type": "application/json"}
        )

def save_parquet_to_s3(data: dict, key: str):
    df = pd.DataFrame([data])
    tbl = pa.Table.from_pandas(df)
    tmp = f"/tmp/{key}.parquet"
    pq.write_table(tbl, tmp)
    with open(tmp, 'rb') as f:
        s3.upload_fileobj(f, PARQUET_OUTPUT_BUCKET, f"{PARQUET_OUTPUT_PREFIX}{key}.parquet")

def lambda_handler(event, context):
    # 1. S3 Event
    bucket = event['Records'][0]['s3']['bucket']['name']
    key    = event['Records'][0]['s3']['object']['key']
    invoice_id = os.path.basename(key).replace('.pdf','')

    # 2. Textract OCR
    tx = boto3.client('textract')
    resp = tx.detect_document_text(
        Document={'S3Object': {'Bucket': bucket, 'Name': key}}
    )
    lines = [b['Text'] for b in resp['Blocks'] if b['BlockType']=='LINE']
    full_text = '\n'.join(lines)

    # 3. Extract fields (more resilient patterns)
    vat_id_raw = extract_field(
        r'(?:VAT\s*(?:ID|No(?:\.|)|Number|Registration))\s*[:\-]?\s*([A-Za-z0-9]+)',
        full_text
    )
    vat_rate_str   = extract_field(
        r'(?:VAT|IVA|Sales\s*Tax)\s*\(?\s*([\d.,]+)\s*%?\s*\)?',
        full_text
    )
    vat_amount_str = extract_field(
        r'(?:VAT|IVA|Sales\s*Tax)\s*\([\d.,]+%\)\s*([\u20AC\d.,$£]+)',
        full_text
    )
    net_total_str  = extract_field(
        r'(?:Subtotal|Net\s*Total|Amount\s*Due)\s*[:\-]?\s*([\u20AC\d.,$£]+)',
        full_text
    )

    # 4. Normalize
    raw_rate   = float(vat_rate_str.replace(',','.')) if vat_rate_str else None
    vat_rate   = raw_rate if raw_rate and raw_rate <= 1 else (raw_rate/100 if raw_rate else None)
    vat_amount = normalize_number(vat_amount_str)
    net_total  = normalize_number(net_total_str)

    # 5. Load config & validate
    allowed = load_allowed_rates()
    reasons, status = [], "PASS"

    if not (vat_id_raw and vat_rate is not None and vat_amount is not None):
        reasons.append("Missing one or more required fields")
        status = "FAIL"

    # Clean & detect country
    vid = (vat_id_raw or "").replace(' ','').upper()
    m = re.match(r'^([A-Z]{2})\d+', vid)
    country = m.group(1) if m else None

    if not country or country not in allowed or vat_rate not in allowed[country]:
        reasons.append(f"Invalid VAT rate {vat_rate} for {country}")
        status = "FAIL"

    if net_total and vat_rate and vat_amount is not None:
        exp = round(net_total * vat_rate, 2)
        if abs(exp - vat_amount) > 0.5:
            reasons.append(f"Math check failed: expected {exp}, got {vat_amount}")
            status = "FAIL"

    # 6. Build & persist result
    result = {
        'invoice_id': invoice_id,
        'country': country or "N/A",
        'vat_rate': vat_rate,
        'vat_amount': vat_amount,
        'supplier_vat_id': vid or "N/A",
        'status': status,
        'reason': "; ".join(reasons) or "All checks passed",
        'ocr_text': full_text,
        'timestamp': datetime.datetime.utcnow().isoformat()
    }
    table.put_item(
        Item={k: (Decimal(str(v)) if isinstance(v, float) else v) for k, v in result.items()}
    )
    save_parquet_to_s3(result, invoice_id)

    # 7. Notify
    send_slack_notification(
        f"Invoice {invoice_id} | Country: {country} | Status: {status} | Reason: {result['reason']}"
    )
    return {'statusCode': 200, 'body': json.dumps('Validation complete')}

