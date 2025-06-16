import boto3
import json

def lambda_handler(event, context):
    # Step 1: Get the file name and bucket from the event
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']

    # Step 2: Set up Textract client
    textract = boto3.client('textract')

    # Step 3: Use Textract to extract text
    response = textract.detect_document_text(
        Document={'S3Object': {'Bucket': bucket, 'Name': key}}
    )

    # Step 4: Get lines of text
    extracted_text = ""
    for block in response['Blocks']:
        if block['BlockType'] == 'LINE':
            extracted_text += block['Text'] + '\n'

    # Step 5: Print it to logs
    print("Extracted text:\n", extracted_text)

    return {
        'statusCode': 200,
        'body': json.dumps('OCR Success')
    }