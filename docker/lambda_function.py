import json
import boto3
import subprocess
import os

s3 = boto3.client('s3')
lambda_client = boto3.client('lambda')

def lambda_handler(event, context):
    # 1) Extract S3 event details
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    filename = os.path.basename(key)
    local_path = f"/tmp/{filename}"
    output_key = f"processed/{filename}"

    # 2) EARLY EXIT: Skip files already in processed/ to avoid double-processing
    if key.startswith("processed/"):
        print("Skipping already-processed file:", key)
        return {"statusCode": 200, "body": "Already processed"}

    # 3) Download the raw PDF from S3
    s3.download_file(bucket, key, local_path)
    print(f"📥 Downloaded {key} to {local_path}")

    # 4) Validate PDF integrity using qpdf
    result = subprocess.run(
        ["qpdf", "--check", local_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"❌ qpdf check failed:\n{result.stderr}")
        raise RuntimeError("PDF validation failed")
    print("✅ qpdf check passed.")

    # 5) Upload validated file to processed/ folder
    s3.upload_file(local_path, bucket, output_key)
    print(f"📤 Uploaded to s3://{bucket}/{output_key}")

    # 6) Manually invoke Textract Lambda asynchronously (fire-and-forget)
    lambda_client.invoke(
        FunctionName='vcm-textract-lambda',
        InvocationType='Event',
        Payload=json.dumps({
            "Records": [{
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": output_key}
                }
            }]
        })
    )
    print("🚀 vcm-textract-lambda triggered.")

    return {
        "statusCode": 200,
        "body": json.dumps("Done")
    }
