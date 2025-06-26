# Copyright © Kevin Della Piazza
# For educational and portfolio use only.
# Do not reuse, copy, or publish without permission.

import json
import os
import boto3

ses = boto3.client('ses')


def lambda_handler(event, context):
    print("Received event:", json.dumps(event))

    for record in event["Records"]:
        try:
            new_image = record['dynamodb']['NewImage']
            status = new_image['status']['S']
            if status == 'FAIL':
                invoice_id = new_image['invoice_id']['S']
                reason = new_image['reason']['S']

                email_body = (
                    f"Invoice {invoice_id} failed validation.\n"
                    f"Reason: {reason}"
                )

                response = ses.send_email(
                    Source=os.environ['ALERT_EMAIL_FROM'],
                    Destination={'ToAddresses': [os.environ['ALERT_EMAIL_TO']]},
                    Message={
                        'Subject': {
                            'Data': f'⚠️ Invoice {invoice_id} Failed Validation'
                        },
                        'Body': {
                            'Text': {'Data': email_body}
                        }
                    }
                )
                print("Email sent:", response['MessageId'])

        except Exception as e:
            print("Error processing record:", e)

    return {'statusCode': 200, 'body': 'Alert(s) processed'}

