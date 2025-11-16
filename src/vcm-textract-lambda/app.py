# Copyright © Kevin Della Piazza
# For educational and portfolio use only.

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
import logging
from decimal import Decimal

# === Logging ===
# Configure logger for CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# === AWS Clients ===
# Initialize clients outside the handler for connection reuse
s3 = boto3.client('s3')
textract = boto3.client('textract')
dynamodb = boto3.resource('dynamodb')
secrets_manager = boto3.client('secretsmanager')

# === CONFIGURATION ===
# Load configuration from environment variables for decoupling
STATUS_TABLE_NAME = os.environ['STATUS_TABLE_NAME']
CONFIG_BUCKET = os.environ['CONFIG_BUCKET']
CONFIG_FILE_KEY = os.environ['CONFIG_FILE_KEY']
PARQUET_BUCKET = os.environ['PARQUET_BUCKET']
PARQUET_PREFIX = os.environ['PARQUET_PREFIX']
SLACK_SECRET_NAME = os.environ['SLACK_SECRET_NAME']

# Initialize DynamoDB Table resource
table = dynamodb.Table(STATUS_TABLE_NAME)

# === GLOBAL CACHE ===
# Cache expensive resources (secrets, HTTP connections) globally
CACHED_SLACK_WEBHOOK_URL = None
HTTP_POOL = urllib3.PoolManager()
CACHED_ALLOWED_RATES = None


def load_allowed_rates():
    """
    Loads the allowed VAT rates configuration from S3.
    Caches the result globally to avoid repeated S3 GetObject calls.
    """
    global CACHED_ALLOWED_RATES
    if CACHED_ALLOWED_RATES:
        logger.info("Using cached VAT rates.")
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
        
        CACHED_ALLOWED_RATES = rates  # Cache the result
        return rates
    except Exception as e:
        logger.error(f"Failed to load config file: {e}")
        raise


def get_slack_webhook():
    """
    Retrieves the Slack Webhook URL from AWS Secrets Manager.
    Caches the secret globally to avoid repeated API calls and costs.
    """
    global CACHED_SLACK_WEBHOOK_URL
    if CACHED_SLACK_WEBHOOK_URL:
        return CACHED_SLACK_WEBHOOK_URL

    logger.info(f"Retrieving secret '{SLACK_SECRET_NAME}' from Secrets Manager...")
    try:
        response = secrets_manager.get_secret_value(SecretId=SLACK_SECRET_NAME)
        secret_string = response['SecretString']
        
        # Handle both plain text secrets and JSON secrets (e.g., {"url":"..."})
        try:
            secret_data = json.loads(secret_string)
            CACHED_SLACK_WEBHOOK_URL = secret_data.get('webhook_url', secret_string)
        except json.JSONDecodeError:
            CACHED_SLACK_WEBHOOK_URL = secret_string
            
        logger.info("Slack secret retrieved and cached.")
        return CACHED_SLACK_WEBHOOK_URL
    except Exception as e:
        logger.error(f"Critical error retrieving Slack secret: {e}")
        raise e


def send_slack_notification(msg):
    """
    Sends a notification to Slack using the securely retrieved webhook.
    Uses a global PoolManager for connection reuse.
    """
    try:
        hook = get_slack_webhook()
        if hook:
            HTTP_POOL.request(
                "POST",
                hook,
                body=json.dumps({"text": msg}).encode(),
                headers={"Content-Type": "application/json"},
            )
            logger.info("Slack notification sent.")
        else:
            logger.warning("SLACK_WEBHOOK_URL not found. Skipping notification.")
    except Exception as e:
        # Log the error but do not fail the Lambda execution
        logger.error(f"Error sending Slack notification: {e}")


def extract_field(pattern, text):
    """Utility function to extract a field using regex."""
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


def extract_currency(text):
    """Utility function to extract a currency symbol."""
    match = re.search(r'([\u20AC$£CHF])[\d,.]+', text)
    return match.group(1) if match else None


def normalize_number(value: str):
    """
    Normalizes a string representation of a number into a float.
    Handles different decimal/thousand separators.
    """
    if not value:
        return None
    v = value.strip().replace('€', '').replace('$', '').replace('£', '').replace('CHF', '')
    
    # Handle ambiguous formats like "1.234,56" (German) vs "1,234.56" (English)
    if '.' in v and ',' in v:
        v = v.replace('.', '').replace(',', '.')  # Assume German-style
    else:
        v = v.replace(',', '')  # Assume English-style or simple number
    
    try:
        return float(v)
    except ValueError:
        logger.warning(f"Could not normalize number: {value}")
        return None


def save_parquet_to_s3(data: dict, key: str):
    """
    Converts the result dictionary to a Parquet file and uploads to S3
    for the analytics layer (Athena).
    """
    try:
        df = pd.DataFrame([data])
        tbl = pa.Table.from_pandas(df)
        tmp_path = f"/tmp/{key}.parquet"
        pq.write_table(tbl, tmp_path)
        
        output_key = f"{PARQUET_PREFIX}{key}.parquet"
        logger.info(f"Uploading Parquet file to s3://{PARQUET_BUCKET}/{output_key}")
        with open(tmp_path, 'rb') as f:
            s3.upload_fileobj(f, PARQUET_BUCKET, output_key)
        os.remove(tmp_path)
    except Exception as e:
        logger.error(f"Failed to save Parquet file: {e}")


def lambda_handler(event, context):
    """
    Main Lambda handler.
    Orchestrates the Textract analysis, data extraction, validation,
    and storage of results.
    """
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    invoice_id = os.path.basename(key).replace('.pdf', '')
    logger.info(f"Processing invoice_id: {invoice_id} from s3://{bucket}/{key}")

    # --- 1. Textract Analysis ---
    try:
        resp = textract.analyze_document(
            Document={'S3Object': {'Bucket': bucket, 'Name': key}},
            FeatureTypes=["FORMS", "TABLES"],
        )
    except Exception:
        logger.warning("AnalyzeDocument failed, falling back to DetectDocumentText.")
        resp = textract.detect_document_text(
            Document={'S3Object': {'Bucket': bucket, 'Name': key}}
        )

    lines = [b['Text'] for b in resp['Blocks'] if b['BlockType'] == 'LINE']
    full_text = '\n'.join(lines)
    logger.info("Text extraction complete.")

    # --- 2. Field Extraction (Regex) ---
    vat_id_raw = extract_field(r'(?:VAT\s*(?:ID|No|Number)|Partita\s*IVA)\s*[:\-]?\s*([A-Za-z0-9]+)', full_text)
    vat_rate_str = extract_field(r'(?:VAT|IVA|TVA|MwSt)\s*\(?\s*([\d.,]+)\s*%?\s*\)?', full_text)
    vat_amount_str = extract_field(r'(?:VAT|IVA|TVA|MwSt)\s*\([\d.,]+%\)\s*([\u20AC\d.,$£CHF]+)', full_text)
    net_total_str = extract_field(r'(?:Subtotal|Net\s*Total|Imponibile)\s*[:\-]?\s*([\u20AC\d.,$£CHF]+)', full_text)

    # --- 3. Data Normalization ---
    raw_rate = float(vat_rate_str.replace(',', '.')) if vat_rate_str else None
    vat_rate = (raw_rate / 100) if (raw_rate and raw_rate > 1) else raw_rate
    vat_amount = normalize_number(vat_amount_str)
    net_total = normalize_number(net_total_str)
    currency_symbol = extract_currency(vat_amount_str or net_total_str or "")
    
    vid = (vat_id_raw or "").replace(' ', '').upper()
    m = re.match(r'^([A-Z]{2})', vid)  # Extract country code from VAT ID
    country = m.group(1) if m else None

    logger.info(f"Extracted data: [Country: {country}, VAT Rate: {vat_rate}, VAT Amount: {vat_amount}, Net: {net_total}]")

    # --- 4. Validation Logic ---
    allowed_rates = load_allowed_rates()
    reasons = []
    status = "PASS"

    if not m:
        reasons.append(f"Invalid VAT ID format: {vid}")
        status = "FAIL"
    elif not (vat_rate is not None and vat_amount is not None):
        reasons.append("Missing VAT rate or VAT amount")
        status = "FAIL"
    elif country not in allowed_rates:
        reasons.append(f"Country code {country} not in configuration")
        status = "FAIL"
    elif vat_rate not in allowed_rates.get(country, []):
        reasons.append(f"Invalid VAT rate {vat_rate} for country {country}")
        status = "FAIL"
    
    # Mathematical check
    if status == "PASS" and net_total:
        expected_vat = round(net_total * vat_rate, 2)
        tolerance = 0.02  # Allow for small rounding differences
        if abs(expected_vat - vat_amount) > tolerance:
            reasons.append(f"Math check failed: expected {expected_vat}, got {vat_amount}")
            status = "FAIL"

    logger.info(f"Validation result: {status}. Reason: {'; '.join(reasons) or 'All checks passed'}")

    # --- 5. Store Results ---
    result_item = {
        'invoice_id': invoice_id,
        'country': country or "N/A",
        'vat_rate': vat_rate,
        'vat_amount': vat_amount,
        'net_total': net_total,
        'currency': currency_symbol or "N/A",
        'supplier_vat_id': vid or "N/A",
        'status': status,
        'reason': "; ".join(reasons) or "All checks passed",
        'ocr_text': full_text[:5000],  # Truncate text to avoid DynamoDB item size limits
        'timestamp': datetime.datetime.utcnow().isoformat(),
    }

    # Convert floats to Decimals for DynamoDB
    dynamo_item = {
        k: (Decimal(str(v)) if isinstance(v, (float, int)) else v)
        for k, v in result_item.items() if v is not None
    }
    
    try:
        # 5a. Save to DynamoDB
        table.put_item(Item=dynamo_item)
        logger.info(f"Result for {invoice_id} saved to DynamoDB.")
        
        # 5b. Save to S3 (Parquet) for Athena
        save_parquet_to_s3(result_item, invoice_id)
        
        # 5c. Send Slack notification
        send_slack_notification(
            f"Invoice {invoice_id} | Country: {country} | Status: {status} | "
            f"Reason: {result_item['reason']}"
        )
        
    except Exception as e:
        logger.error(f"Failed to store results for {invoice_id}: {e}")
        raise

    return {'statusCode': 200, 'body': json.dumps('Validation complete')}