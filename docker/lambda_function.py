import json
import boto3
import subprocess
import os
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)
s3 = boto3.client('s3')

LAMBDA_PYTHON_PATH = "/usr/local/bin/python3"
QPDF_PATH = "/usr/bin/qpdf"

def lambda_handler(event, context):
    """
    Main Lambda handler for preprocessing PDF invoices. It downloads a PDF from S3,
    adds a text layer using OCR if needed, validates it, and uploads the
    processed file back to a different S3 prefix.
    """
    local_path = None
    ocr_output_path = None
    try:
        record = event['Records'][0]
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']

        if key.startswith("processed/"):
            logger.info(f"⏩ File is already in the 'processed/' folder. Skipping: {key}")
            return {"statusCode": 200, "body": "File already processed."}

        filename = os.path.basename(key)
        local_path = f"/tmp/{filename}"
        ocr_output_path = f"/tmp/ocr_{filename}"

        logger.info(f"📥 Downloading s3://{bucket}/{key} to {local_path}...")
        s3.download_file(bucket, key, local_path)
        logger.info("✅ Download complete.")

        logger.info(f"⚙️ Running OCRmyPDF as a Python module on {local_path}...")
        command = [
            LAMBDA_PYTHON_PATH,
            "-m", "ocrmypdf",
            "--skip-text",
            "--deskew",
            "--language", "eng+ita+fra+deu",
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
            raise RuntimeError(f"OCRmyPDF failed with error: {result.stderr}")

        logger.info(f"✅ OCR successfully completed. Output:\n{result.stdout}")

        logger.info(f"🔍 Validating OCR'd file {ocr_output_path} with qpdf...")
        qpdf_result = subprocess.run(
            [QPDF_PATH, "--check", ocr_output_path],
            capture_output=True,
            text=True,
            check=False
        )
        if qpdf_result.returncode != 0:
            logger.error(f"❌ PDF validation failed for {ocr_output_path}.")
            logger.error(f"Stderr: {qpdf_result.stderr}")
            raise RuntimeError(f"PDF validation with qpdf failed: {qpdf_result.stderr}")
        
        logger.info("✅ qpdf validation passed.")
        
        output_key = f"processed/{filename}"
        logger.info(f"📤 Uploading processed file to s3://{bucket}/{output_key}...")
        s3.upload_file(ocr_output_path, bucket, output_key)
        logger.info("✅ Upload complete. The pipeline has finished successfully!")

        return {
            "statusCode": 200,
            "body": json.dumps({"status": "Success", "processed_file": output_key})
        }

    except Exception as e:
        logger.error(f"❌ Unrecoverable error in Lambda handler: {e}")
        raise e
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)
        if ocr_output_path and os.path.exists(ocr_output_path):
            os.remove(ocr_output_path)
        logger.info("🧹 Cleaned up temporary files.")
        