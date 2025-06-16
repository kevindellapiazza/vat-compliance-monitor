import boto3
import csv
import json
import re
import datetime
from decimal import Decimal

s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('vcm-invoice-status')

# === CONFIG ===
CONFIG_BUCKET = 'vcm-config-kevin'
CONFIG_FILE = 'allowed-vat-rates.csv'

# Load VAT rates from CSV stored in S3
def load_allowed_rates():
    response = s3.get_object(Bucket=CONFIG_BUCKET, Key=CONFIG_FILE)
    lines = response['Body'].read().decode('utf-8').splitlines()
    reader = csv.DictReader(lines)
    rates = {}
    for row in reader:
        country = row['country']
        rate = float(row['rate'])
        rates.setdefault(country, []).append(rate)
    return rates

# Helper to extract a field with regex
def extract_field(pattern, text):
    match = re.search(pattern, text)
    return match.group(1) if match else None

def lambda_handler(event, context):
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    invoice_id = key.split('/')[-1].replace('.pdf', '')

    # OCR using Textract
    textract = boto3.client('textract')
    response = textract.detect_document_text(
        Document={'S3Object': {'Bucket': bucket, 'Name': key}}
    )
    lines = [b['Text'] for b in response['Blocks'] if b['BlockType'] == 'LINE']
    full_text = '\n'.join(lines)

    # Extract fields
    vat_id = extract_field(r'Supplier VAT ID\s*(\w+)', full_text)
    vat_rate_str = extract_field(r'VAT\s*\(([\d.,]+)%\)', full_text)
    vat_amount_str = extract_field(r'VAT\s*\([\d.,]+%\)\s*([€\d.,]+)', full_text)
    net_total_str = extract_field(r'Subtotal\s*([€\d.,]+)', full_text)

    # Convert values to numbers
    def normalize(value):
        return float(value.replace('€', '').replace(',', '').strip()) if value else None

    vat_rate = float(vat_rate_str.replace(',', '.')) / 100 if vat_rate_str else None
    vat_amount = normalize(vat_amount_str)
    net_total = normalize(net_total_str)

    # Load allowed rates from S3
    allowed_rates = load_allowed_rates()

    # === Validation ===
    reasons = []
    status = "PASS"

    if not (vat_id and vat_rate and vat_amount):
        reasons.append("Missing one or more required fields")
        status = "FAIL"

    country = vat_id[:2] if vat_id else None
    if country not in allowed_rates or vat_rate not in allowed_rates.get(country, []):
        reasons.append(f"Invalid VAT rate {vat_rate} for {country}")
        status = "FAIL"

    if net_total and vat_rate and vat_amount:
        expected = round(net_total * vat_rate, 2)
        if abs(expected - vat_amount) > 0.5:
            reasons.append(f"Math check failed: expected {expected}, got {vat_amount}")
            status = "FAIL"

    # === Save result to DynamoDB ===
    table.put_item(Item={
        'invoice_id': invoice_id,
        'vat_rate': Decimal(str(vat_rate)) if vat_rate else None,
        'vat_amount': Decimal(str(vat_amount)) if vat_amount else None,
        'supplier_vat_id': vat_id or "N/A",
        'status': status,
        'reason': "; ".join(reasons) or "All checks passed",
        'ocr_text': full_text,
        'timestamp': datetime.datetime.utcnow().isoformat()
    })

    print("Validation result:", status, reasons)

    return {
        'statusCode': 200,
        'body': json.dumps('Validation complete')
    }
