import boto3
import csv
import json
import re
import datetime
import os
import urllib3
from decimal import Decimal

# === AWS Clients ===
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('vcm-invoice-status')

# === CONFIG ===
CONFIG_BUCKET = 'vcm-config-kevin'
CONFIG_FILE = 'allowed-vat-rates.csv'

# === Load VAT rates from CSV stored in S3 ===
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

# === Helper to extract a field with regex ===
def extract_field(pattern, text):
    match = re.search(pattern, text)
    return match.group(1) if match else None

# === Send message to Slack ===
def send_slack_notification(message):
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook_url:
        http = urllib3.PoolManager()
        payload = {"text": message}
        encoded_data = json.dumps(payload).encode("utf-8")
        http.request("POST", webhook_url, body=encoded_data, headers={"Content-Type": "application/json"})

# === Lambda Handler ===
def lambda_handler(event, context):
    # Step 1: Get bucket and file key from S3 event
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    invoice_id = key.split('/')[-1].replace('.pdf', '')

    # Step 2: OCR with Textract
    textract = boto3.client('textract')
    response = textract.detect_document_text(
        Document={'S3Object': {'Bucket': bucket, 'Name': key}}
    )
    lines = [b['Text'] for b in response['Blocks'] if b['BlockType'] == 'LINE']
    full_text = '\n'.join(lines)

    # Step 3: Extract key fields from OCR text
    vat_id = extract_field(r'Supplier VAT ID\s*(\w+)', full_text)
    vat_rate_str = extract_field(r'VAT\s*\(([\d.,]+)%\)', full_text)
    vat_amount_str = extract_field(r'VAT\s*\([\d.,]+%\)\s*([\u20AC\d.,]+)', full_text)
    net_total_str = extract_field(r'Subtotal\s*([\u20AC\d.,]+)', full_text)

    # Step 4: Normalize numbers
    def normalize(value):
        return float(value.replace('€', '').replace(',', '').strip()) if value else None

    vat_rate = float(vat_rate_str.replace(',', '.')) / 100 if vat_rate_str else None
    vat_amount = normalize(vat_amount_str)
    net_total = normalize(net_total_str)

    # Step 5: Load config from S3
    allowed_rates = load_allowed_rates()

    # Step 6: Run validations
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

    # Step 7: Write result to DynamoDB
    table.put_item(Item={
        'invoice_id': invoice_id,
        'country': country or "N/A",
        'vat_rate': Decimal(str(vat_rate)) if vat_rate else None,
        'vat_amount': Decimal(str(vat_amount)) if vat_amount else None,
        'supplier_vat_id': vat_id or "N/A",
        'status': status,
        'reason': "; ".join(reasons) or "All checks passed",
        'ocr_text': full_text,
        'timestamp': datetime.datetime.utcnow().isoformat()
    })

    # Step 8: Log and notify
    print("Validation result:", status, reasons)
    slack_message = f"✅ Invoice {invoice_id} validated. Country: {country}, Status: {status}, Reason: {'; '.join(reasons) or 'All checks passed'}"
    send_slack_notification(slack_message)

    return {
        'statusCode': 200,
        'body': json.dumps('Validation complete')
    }