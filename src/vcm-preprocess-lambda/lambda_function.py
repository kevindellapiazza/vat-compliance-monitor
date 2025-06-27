import json
import boto3
import subprocess
import os

s3 = boto3.client('s3')
lambda_client = boto3.client('lambda')

def lambda_handler(event, context):
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']

    filename = os.path.basename(key)
    local_path = f"/tmp/{filename}"
    output_key = f"processed/{filename}"

    # Step 1: Download
    s3.download_file(bucket, key, local_path)
    print(f"📥 Downloaded {key} from S3")

    # Step 2: Run qpdf check
    try:
        result = subprocess.run(["qpdf", "--check", local_path], capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"❌ qpdf check failed:\n{result.stderr}")
        print("✅ qpdf check passed.")
    except Exception as e:
        print(f"⚠️ Error checking PDF: {e}")
        raise e

    # Step 3: Upload to processed/
    s3.upload_file(local_path, bucket, output_key)
    print(f"📤 Uploaded to {output_key}")

    # Step 4: Trigger textract Lambda manually
    try:
        lambda_client.invoke(
            FunctionName='vcm-textract-lambda',
            InvocationType='Event',  # async
            Payload=json.dumps({
                "Records": [{
                    "s3": {
                        "bucket": {"name": bucket},
                        "object": {"key": output_key}
                    }
                }]
            })
        )
        print("🚀 Textract Lambda triggered.")
    except Exception as e:
        print(f"❌ Failed to trigger textract Lambda: {e}")
        raise e

    return {
        'statusCode': 200,
        'body': json.dumps('Preprocessing complete and Textract triggered.')
    }


