# Copyright © Kevin Della Piazza
# For educational and portfolio use only.

import boto3
import csv
import json
import datetime
import os
import urllib3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import logging
from decimal import Decimal

# === Logging ===
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# === AWS Clients ===
s3 = boto3.client('s3')
textract = boto3.client('textract')
dynamodb = boto3.resource('dynamodb')
secrets_manager = boto3.client('secretsmanager')

# === BEDROCK CLIENT ===
bedrock = boto3.client(service_name='bedrock-runtime', region_name='eu-central-1')

# === CONFIGURATION ===
STATUS_TABLE_NAME = os.environ['STATUS_TABLE_NAME']
CONFIG_BUCKET = os.environ['CONFIG_BUCKET']
CONFIG_FILE_KEY = os.environ['CONFIG_FILE_KEY']
PARQUET_BUCKET = os.environ['PARQUET_BUCKET']
PARQUET_PREFIX = os.environ['PARQUET_PREFIX']
SLACK_SECRET_NAME = os.environ['SLACK_SECRET_NAME']

table = dynamodb.Table(STATUS_TABLE_NAME)

# === GLOBAL CACHE ===
CACHED_SLACK_WEBHOOK_URL = None
HTTP_POOL = urllib3.PoolManager()
CACHED_ALLOWED_RATES = None

# --- HELPER FUNCTIONS ---

def load_allowed_rates():
    global CACHED_ALLOWED_RATES
    if CACHED_ALLOWED_RATES:
        return CACHED_ALLOWED_RATES

    logger.info(f"Loading VAT rates from s3://{CONFIG_BUCKET}/{CONFIG_FILE_KEY}")
    try:
        resp = s3.get_object(Bucket=CONFIG_BUCKET, Key=CONFIG_FILE_KEY)
        lines = resp['Body'].read().decode('utf-8').splitlines()
        reader = csv.DictReader(lines)
        rates = {}
        for row in reader:
            country = row['country'].upper()
            rate = float(row['rate'])
            rates.setdefault(country, []).append(rate)

        CACHED_ALLOWED_RATES = rates
        return rates
    except Exception as e:
        logger.error(f"Failed to load config file: {e}")
        raise

def get_slack_webhook():
    global CACHED_SLACK_WEBHOOK_URL
    if CACHED_SLACK_WEBHOOK_URL:
        return CACHED_SLACK_WEBHOOK_URL

    try:
        response = secrets_manager.get_secret_value(SecretId=SLACK_SECRET_NAME)
        secret_string = response['SecretString']
        try:
            secret_data = json.loads(secret_string)
            CACHED_SLACK_WEBHOOK_URL = secret_data.get('webhook_url', secret_string)
        except json.JSONDecodeError:
            CACHED_SLACK_WEBHOOK_URL = secret_string
        return CACHED_SLACK_WEBHOOK_URL
    except Exception as e:
        logger.error(f"Error retrieving Slack secret: {e}")
        return None

def send_slack_notification(msg):
    try:
        hook = get_slack_webhook()
        if hook:
            HTTP_POOL.request(
                "POST", hook,
                body=json.dumps({"text": msg}).encode(),
                headers={"Content-Type": "application/json"}
            )
    except Exception as e:
        logger.error(f"Error sending Slack notification: {e}")

def save_parquet_to_s3(data: dict, key: str):
    try:
        df = pd.DataFrame([data])
        tbl = pa.Table.from_pandas(df)
        tmp_path = f"/tmp/{key}.parquet"
        pq.write_table(tbl, tmp_path)

        output_key = f"{PARQUET_PREFIX}{key}.parquet"
        with open(tmp_path, 'rb') as f:
            s3.upload_fileobj(f, PARQUET_BUCKET, output_key)
        os.remove(tmp_path)
    except Exception as e:
        logger.error(f"Failed to save Parquet: {e}")

def is_valid_pdf(bucket, key):
    try:
        # read 4 byte (Range request)
        response = s3.get_object(
            Bucket=bucket,
            Key=key,
            Range='bytes=0-4'
        )
        header = response['Body'].read()
        if header.startswith(b'%PDF'):
            return True
        return False
    except Exception as e:
        logger.error(f"Error checking file header: {e}")
        return False

# --- AI EXTRACTION ENGINE (Claude 3 Haiku) ---

def extract_invoice_data_with_ai(ocr_text):
    """
    Uses AWS Bedrock to extract structured data from OCR text.
    Replaces fragile Regex logic with semantic understanding.
    """
    # === PROMPT (Formatted to avoid E501 Line too long errors) ===
    prompt = f"""
    You are a financial AI. Extract these fields from the invoice text below into JSON:
    1. supplier_vat_id: The full VAT number (e.g., IT123456789 or CHE-123.456.789).
    2. vat_rate: The tax percentage as a decimal (e.g. 0.22). If multiple, take the main one.
    3. vat_amount: The tax amount (numeric).
    4. net_total: The total amount BEFORE tax (numeric).
    5. currency: Symbol (e.g., €, $, £, CHF).
    6. country: The 2-letter ISO country code.
    RULE: If VAT ID starts with "CHE", country MUST be "CH".
    Return ONLY valid JSON. If a field is missing, use null.
    TEXT:
    {ocr_text[:15000]}
    """
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0
    })

    try:
        response = bedrock.invoke_model(
            modelId="anthropic.claude-3-haiku-20240307-v1:0",
            body=body
        )

        response_body = json.loads(response.get("body").read())
        ai_result = response_body["content"][0]["text"]

        # Clean up markdown
        ai_result = ai_result.replace("```json", "").replace("```", "").strip()

        return json.loads(ai_result)

    except Exception as e:
        logger.error(f"AI Extraction Failed: {e}")
        return None

# --- MAIN HANDLER ---

def lambda_handler(event, context):
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    invoice_id = os.path.basename(key).replace('.pdf', '')

    # === SECURITY CHECK: Demo Mode Limit ===
    # If file > 2MB, block it to prevent cost abuse
    if event['Records'][0]['s3']['object']['size'] > 2 * 1024 * 1024:
        logger.warning(f"File {key} too large (>2MB). Skipping for Demo Mode.")
        return {'statusCode': 400, 'body': 'File too large'}

    # If file is not a PDF, stop processing
    if not is_valid_pdf(bucket, key):
        logger.warning(f"File {key} is NOT a valid PDF. Skipping.")
        # s3.delete_object(Bucket=bucket, Key=key)
        return {'statusCode': 400, 'body': 'Invalid file type'}

    logger.info(f"Processing {invoice_id} from s3://{bucket}/{key}")

    # 1. Textract (OCR Only)
    try:
        # DetectDocumentText because we only need raw text for the LLM
        resp = textract.detect_document_text(
            Document={'S3Object': {'Bucket': bucket, 'Name': key}}
        )
        lines = [b['Text'] for b in resp['Blocks'] if b['BlockType'] == 'LINE']
        full_text = '\n'.join(lines)
        logger.info("Text extraction complete.")
    except Exception as e:
        logger.error(f"Textract failed: {e}")
        raise e

    # 2. AI Extraction (Bedrock)
    logger.info("🤖 Invoking Bedrock AI...")
    extracted = extract_invoice_data_with_ai(full_text)

    # Variables initialization
    country = None
    vat_rate = None
    vat_amount = None
    net_total = None
    currency_symbol = None
    vid = None
    reasons = []
    status = "PASS"

    if extracted:
        logger.info(f"✅ AI Data: {extracted}")
        country = extracted.get("country")
        if country and country.upper() == 'CHE':
            country = 'CH'
        vat_rate = extracted.get("vat_rate")
        vat_amount = extracted.get("vat_amount")
        net_total = extracted.get("net_total")
        currency_symbol = extracted.get("currency")
        vid = extracted.get("supplier_vat_id")
    else:
        logger.warning("⚠️ AI Extraction failed or returned None.")
        status = "FAIL"
        reasons.append("AI Extraction Failed")

    # 3. Validation Logic (Deterministic)
    allowed_rates = load_allowed_rates()

    if status != "FAIL": # Only validate if AI succeeded
        if not vid:
            reasons.append("Missing VAT ID")
            status = "FAIL"
        elif not (vat_rate is not None and vat_amount is not None):
            reasons.append("Missing VAT rate or amount")
            status = "FAIL"
        elif country not in allowed_rates:
            reasons.append(f"Country code '{country}' not in configuration")
            status = "FAIL"
        elif vat_rate not in allowed_rates.get(country, []):
            reasons.append(f"Invalid VAT rate {vat_rate} for country {country}")
            status = "FAIL"

        # Mathematical Check
        if status == "PASS" and net_total:
            expected_vat = round(net_total * vat_rate, 2)
            # Tolerance 0.05 for rounding differences
            if abs(expected_vat - vat_amount) > 0.05:
                reasons.append(f"Math check failed: expected {expected_vat}, got {vat_amount}")
                status = "FAIL"

    logger.info(f"Validation: {status}. Reason: {reasons}")

    # 4. Store Results
    result_item = {
        'invoice_id': invoice_id,
        'country': country or "N/A",
        'vat_rate': vat_rate,
        'vat_amount': vat_amount,
        'net_total': net_total,
        'currency': currency_symbol or "N/A",
        'supplier_vat_id': vid or "N/A",
        'status': status,
        'reason': "; ".join(reasons) or "Passed",
        'ocr_text': full_text[:3000], # Truncate for DynamoDB limits
        'timestamp': datetime.datetime.utcnow().isoformat(),
    }

    # DynamoDB Decimal conversion
    dynamo_item = {
        k: (Decimal(str(v)) if isinstance(v, (float, int)) else v)
        for k, v in result_item.items() if v is not None
    }

    try:
        # Save to DynamoDB
        table.put_item(Item=dynamo_item)

        # Save to S3 (Parquet)
        save_parquet_to_s3(result_item, invoice_id)

        # Notify Slack
        send_slack_notification(
            f"Invoice {invoice_id} | {country} | {status} | {result_item['reason']}"
        )

    except Exception as e:
        logger.error(f"Storage failed: {e}")
        raise

    return {'statusCode': 200, 'body': json.dumps('Validation complete')}
