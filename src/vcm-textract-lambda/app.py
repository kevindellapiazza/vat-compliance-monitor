"""
VAT Compliance Monitor (VCM) - Extraction Engine
Version: 2.0 (March 2026)
Architect: Kevin Della Piazza

This Lambda orchestrates a hybrid extraction-validation pipeline:
1. Vision Layer: Amazon Textract for raw OCR.
2. Reasoning Layer: Claude 4.5 Haiku (Few-Shot) for semantic parsing.
3. Guardrail Layer: Deterministic math & regex validation.
"""

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
import re

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

# === VALID ID PATTER FOR EU VAT ID ===
VAT_PATTERNS = {
    'IT': r'^IT[0-9]{11}$',
    'DE': r'^DE[0-9]{9}$',
    'FR': r'^FR[A-Z0-9]{2}[0-9]{9}$',
    'ES': r'^ES[A-Z0-9][0-9]{7}[A-Z0-9]$',
    'CH': r'^CHE[0-9]{9}(MWST|TVA|IVA)?$',
    'BE': r'^BE[0-9]{10}$'
}


# --- HELPER FUNCTIONS ---

def load_allowed_rates():
    """
    Retrieves VAT rate configurations from S3.
    Implements a basic in-memory cache to optimize performance.
    """
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
    """
    Securely fetches the Slack Webhook URL from AWS Secrets Manager.
    Uses caching to avoid repeated SecretValue calls.
    """
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
    """Dispatches real-time alerts to Slack channel."""
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
    """
    Persists validated data in Apache Parquet format to the Data Lake.
    Enables high-performance analytics via Amazon Athena.
    """
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
    """
    Ensures the file is a genuine PDF by inspecting the file header.
    """
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

def validate_vat_format(vid,country):
    """
    Performs deterministic syntax validation based on national standards.
    """
    if not vid or not country:
        return False
    pattern = VAT_PATTERNS.get(country.upper())
    if not pattern:
        return True
    clean_id = vid.replace(" ", "").replace(".", "").upper()
    return bool(re.match(pattern, clean_id))


# --- AI EXTRACTION ENGINE (Claude 4.5 Haiku) ---

def extract_invoice_data_with_ai(ocr_text):
    # === FEW SHOT EXAMPLES ===
    examples = """
<examples>
    <example>
        <input>Fattura n. 123 - Rossi SRL - IT01234567890 - 100.00 EUR -
        IVA 22%: 22.00 - Totale: 122.00</input>
        <output>
        {"supplier_vat_id": "IT01234567890", "vat_rate": 0.22, "vat_amount": 22.0,
        "net_total": 100.0, "total_amount": 122.0, "currency": "€", "country": "IT"}
        </output>
    </example>
    <example>
        <input>CHE-999.888.777 MWST - Subtotal: 200.00 CHF -
        Tax 7.7%: 15.40 - Total: 215.40</input>
        <output>
        {"supplier_vat_id": "CHE999888777", "vat_rate": 0.077, "vat_amount": 15.4,
        "net_total": 200.0, "total_amount": 215.4, "currency": "CHF", "country": "CH"}
        </output>
    </example>
</examples>
"""

    # === PROMPT ===
    prompt_content = f"""
{examples}

<instructions>
Extract these fields from the invoice text below into JSON:
1. supplier_vat_id (no spaces or dots)
2. vat_rate (decimal, e.g. 0.22)
3. vat_amount (numeric)
4. net_total (numeric)
5. total_amount (numeric)
6. currency (symbol or code)
7. country (ISO 2-letter)
RULE: If VAT ID starts with "CHE", country MUST be "CH".
Return ONLY valid JSON. No preamble, no markdown.
</instructions>

<text_to_analyze>
{ocr_text[:15000]}
</text_to_analyze>
"""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "system": (
        "You are a professional financial auditor."
        "You output only raw JSON blocks based on the examples provided."
        ),
        "messages": [{"role": "user", "content": prompt_content}],
        "temperature": 0
    })

    try:
        response = bedrock.invoke_model(
            modelId="arn:aws:bedrock:eu-central-1:099220985688:inference-profile/eu.anthropic.claude-haiku-4-5-20251001-v1:0",
            body=body
        )
        response_body = json.loads(response.get("body").read())
        ai_result = response_body["content"][0]["text"].strip()

        if "```" in ai_result:
            ai_result = ai_result.split("```json")[-1].split("```")[0].strip()

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
    vat_rate = 0.0
    vat_amount = 0.0
    net_total = 0.0
    total_gross = 0.0
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
        total_gross = extracted.get("total_amount")
    else:
        logger.warning("⚠️ AI Extraction failed or returned None.")
        status = "FAIL"
        reasons.append("AI Extraction Failed")

    # 3. Validation Logic (Deterministic Guardrails)
    allowed_rates = load_allowed_rates()

    if status != "FAIL":
        # --- LAYER 1: Syntax Validation (VAT ID) ---
        # We verify that the extracted VAT ID matches the official national format using Regex.
        if not vid:
            reasons.append("Missing VAT ID")
            status = "FAIL"
        elif not validate_vat_format(vid, country):
            reasons.append(f"Invalid VAT ID format for {country}")
            status = "FAIL"

        # --- LAYER 2: Business Policy Validation (VAT Rates) ---
        # We cross-check the extracted rate against the allowed VAT rates
        # configuration for the specific country.
        if country not in allowed_rates:
            reasons.append(f"Country code '{country}' not supported")
            status = "FAIL"
        elif vat_rate not in allowed_rates.get(country, []):
            reasons.append(f"Invalid VAT rate {vat_rate} for country {country}")
            status = "FAIL"

        # --- LAYER 3: Mathematical Validation ---
        # Since LLMs are non-deterministic, enforce a consistent algebraic loop.
        valid_fields = [net_total, vat_rate, vat_amount, total_gross]
        if status == "PASS" and all(v is not None for v in valid_fields):

            # Sub-check 1: Percentage Consistency (Net * Rate = VAT)
            expected_vat = round(net_total * vat_rate, 2)
            if abs(expected_vat - vat_amount) > 0.05:
                reasons.append(f"Math Fail: Net * Rate ({expected_vat}) != VAT ({vat_amount})")
                status = "FAIL"

            # Sub-check 2: Summation Integrity (Net + VAT = Total)
            expected_total = round(net_total + vat_amount, 2)
            if abs(expected_total - total_gross) > 0.05:
                reasons.append(f"Math Fail: Net + VAT ({expected_total}) != Total ({total_gross})")
                status = "FAIL"

            # Sub-check 3: Inverse Verification (Total - VAT = Net)
            # Ensures no phantom fees were added during the process.
            expected_net = round(total_gross - vat_amount, 2)
            if abs(expected_net - net_total) > 0.05:
                reasons.append(f"Math Fail: Total - VAT ({expected_net}) != Net ({net_total})")
                status = "FAIL"

    logger.info(f"Validation: {status}. Reason: {reasons}")

    # 4. Store Results
    result_item = {
        'invoice_id': invoice_id,
        'country': country or "N/A",
        'vat_rate': vat_rate,
        'vat_amount': vat_amount,
        'net_total': net_total,
        'total_amount': total_gross,
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
