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


def extract_currency(text):
    match = re.search(r'([\u20AC$£CHF])[\d,.]+', text)
    return match.group(1) if match else None


def normalize_number(value: str):
    if not value:
        return None
    v = (
        value.strip()
        .replace('€', '')
        .replace('$', '')
        .replace('£', '')
        .replace('CHF', '')
    )
    if '.' in v and ',' in v:
        v = v.replace('.', '').replace(',', '.')
    else:
        v = v.replace(',', '')
    try:
        return float(v)
    except Exception:
        return None


def send_slack_notification(msg):
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if hook:
        http = urllib3.PoolManager()
        http.request(
            "POST",
            hook,
            body=json.dumps({"text": msg}).encode(),
            headers={"Content-Type": "application/json"},
        )


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
    try:
        resp = tx.analyze_document(
            Document={'S3Object': {'Bucket': bucket, 'Name': key}},
            FeatureTypes=["FORMS", "TABLES"],
        )
    except Exception:
        resp = tx.detect_document_text(
            Document={'S3Object': {'Bucket': bucket, 'Name': key}}
        )

    lines = [b['Text'] for b in resp['Blocks'] if b['BlockType'] == 'LINE']
    full_text = '\n'.join(lines)
    print("✅ Extracted Text from Invoice:")
    print(full_text)

    vat_id_raw = extract_field(
        r'(?:VAT\s*(?:ID|No(?:\.|)|Number|Registration)|TVA|USt-IdNr|Partita\s*IVA)'
        r'\s*[:\-]?\s*([A-Za-z0-9]+)',
        full_text,
    )
    vat_rate_str = extract_field(
        r'(?:VAT|IVA|TVA|MwSt|Sales\s*Tax)\s*\(?\s*([\d.,]+)\s*%?\s*\)?',
        full_text,
    )
    vat_amount_str = extract_field(
        r'(?:VAT|IVA|TVA|MwSt|Sales\s*Tax)\s*\([\d.,]+%\)\s*([\u20AC\d.,$£CHF]+)',
        full_text,
    )
    net_total_str = extract_field(
        r'(?:Subtotal|Net\s*Total|Amount\s*Due|Totale\s*Netto|Total\s*HT)'
        r'\s*[:\-]?\s*([\u20AC\d.,$£CHF]+)',
        full_text,
    )

    raw_rate = float(vat_rate_str.replace(',', '.')) if vat_rate_str else None
    vat_rate = (
        raw_rate if raw_rate and raw_rate <= 1 else (raw_rate / 100 if raw_rate else None)
    )
    vat_amount = normalize_number(vat_amount_str)
    net_total = normalize_number(net_total_str)
    currency_symbol = extract_currency(vat_amount_str or net_total_str or "")

    allowed = load_allowed_rates()
    reasons, status = [], "PASS"

    vid = (vat_id_raw or "").replace(' ', '').upper()
    m = re.match(r'^([A-Z]{2})\d+', vid)
    country = m.group(1) if m else None

    if not m:
        reasons.append(f"Invalid VAT ID format: {vid}")
        status = "FAIL"

    if status == "FAIL":
        result = {
            'invoice_id': invoice_id,
            'country': country or "N/A",
            'vat_rate': vat_rate,
            'vat_amount': vat_amount,
            'currency': currency_symbol or "N/A",
            'supplier_vat_id': vid or "N/A",
            'status': status,
            'reason': "; ".join(reasons),
            'ocr_text': full_text,
            'timestamp': datetime.datetime.utcnow().isoformat(),
        }
        table.put_item(
            Item={
                k: (Decimal(str(v)) if isinstance(v, float) else v)
                for k, v in result.items()
            }
        )
        save_parquet_to_s3(result, invoice_id)
        send_slack_notification(
            f"Invoice {invoice_id} | Country: {country} | Status: {status} | "
            f"Reason: {result['reason']}"
        )
        return {'statusCode': 200, 'body': json.dumps('Validation complete')}

    if not (vat_rate is not None and vat_amount is not None):
        reasons.append("Missing VAT rate or VAT amount")
        status = "FAIL"

    if country not in allowed or vat_rate not in allowed.get(country, []):
        reasons.append(f"Invalid VAT rate {vat_rate} for {country}")
        status = "FAIL"

    if net_total and vat_rate and vat_amount is not None:
        expected = round(net_total * vat_rate, 2)
        tolerance = 0.02
        if abs(expected - vat_amount) > tolerance:
            reasons.append(f"Math check failed: expected {expected}, got {vat_amount}")
            status = "FAIL"

    result = {
        'invoice_id': invoice_id,
        'country': country or "N/A",
        'vat_rate': vat_rate,
        'vat_amount': vat_amount,
        'currency': currency_symbol or "N/A",
        'supplier_vat_id': vid or "N/A",
        'status': status,
        'reason': "; ".join(reasons) or "All checks passed",
        'ocr_text': full_text,
        'timestamp': datetime.datetime.utcnow().isoformat(),
    }

    table.put_item(
        Item={
            k: (Decimal(str(v)) if isinstance(v, float) else v)
            for k, v in result.items()
        }
    )
    save_parquet_to_s3(result, invoice_id)

    send_slack_notification(
        f"Invoice {invoice_id} | Country: {country} | Status: {status} | "
        f"Reason: {result['reason']}"
    )

    return {'statusCode': 200, 'body': json.dumps('Validation complete')}


