import json
import boto3
import subprocess
import os
import logging

# Configure logger for detailed CloudWatch logs
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize S3 client outside the handler for reuse
s3 = boto3.client('s3')

# Define paths to executables within the Docker container
LAMBDA_PYTHON_PATH = "/usr/local/bin/python3"
QPDF_PATH = "/usr/bin/qpdf"


def lambda_handler(event, context):
    """
    Main Lambda handler for preprocessing PDF invoices.
    1. Downloads a PDF from the 'raw/' S3 prefix.
    2. Uses 'ocrmypdf' (via subprocess) to add a text layer.
    3. Validates the processed PDF using 'qpdf'.
    4. Uploads the processed file to the 'processed/' S3 prefix.
    """
    local_path = None
    ocr_output_path = None

    try:
        # 1. Parse S3 event record
        record = event['Records'][0]
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        filename = os.path.basename(key)

        # Define temporary file paths in the Lambda's /tmp directory
        local_path = f"/tmp/{filename}"
        ocr_output_path = f"/tmp/ocr_{filename}"

        logger.info(f"📥 Downloading s3://{bucket}/{key} to {local_path}...")
        s3.download_file(bucket, key, local_path)
        logger.info("✅ Download complete.")

        # 2. Run OCRmyPDF
        # This adds a text layer to scanned PDFs, making them machine-readable.
        logger.info(f"⚙️ Running OCRmyPDF on {local_path}...")
        command = [
            LAMBDA_PYTHON_PATH,
            "-m", "ocrmypdf",
            "--skip-text",        # Skip pages that already have text
            "--deskew",           # Correct skewed scans
            "--language", "eng+ita+fra+deu", # Support multiple languages
            local_path,
            ocr_output_path
        ]

        result = subprocess.run(
            command, capture_output=True, text=True, check=False
        )

        if result.returncode != 0:
            logger.error(f"❌ OCRmyPDF execution failed. Return code: {result.returncode}")
            logger.error(f"Stderr: {result.stderr}")
            logger.error(f"Stdout: {result.stdout}")
            raise RuntimeError(f"OCRmyPDF failed: {result.stderr}")

        logger.info(f"✅ OCR successfully completed. Stdout:\n{result.stdout}")

        # 3. Validate processed PDF
        # Ensures the output file is not corrupted before uploading.
        logger.info(f"🔍 Validating OCR'd file {ocr_output_path} with qpdf...")
        qpdf_result = subprocess.run(
            [QPDF_PATH, "--check", ocr_output_path],
            capture_output=True, text=True, check=False
        )

        if qpdf_result.returncode != 0:
            logger.error(f"❌ PDF validation failed for {ocr_output_path}.")
            logger.error(f"Stderr: {qpdf_result.stderr}")
            raise RuntimeError(f"PDF validation with qpdf failed: {qpdf_result.stderr}")

        logger.info("✅ qpdf validation passed.")

        # 4. Upload processed file
        output_key = f"processed/{filename}"
        logger.info(f"📤 Uploading processed file to s3://{bucket}/{output_key}...")
        s3.upload_file(ocr_output_path, bucket, output_key)
        logger.info("✅ Upload complete. Preprocessing finished.")

        return {
            "statusCode": 200,
            "body": json.dumps({"status": "Success", "processed_file": output_key})
        }

    except Exception as e:
        logger.error(f"❌ Unrecoverable error in Lambda handler: {e}", exc_info=True)
        raise e  # Re-raise exception to mark the Lambda execution as failed

    finally:
        # 5. Cleanup
        # Always remove temporary files from /tmp
        if local_path and os.path.exists(local_path):
            os.remove(local_path)
        if ocr_output_path and os.path.exists(ocr_output_path):
            os.remove(ocr_output_path)
        logger.info("🧹 Cleaned up temporary files.")
