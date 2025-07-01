import json
import os
import boto3
import logging

# Configure the logger for clear, structured logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize the SES client once outside the handler for performance
ses = boto3.client('ses')

def lambda_handler(event, context):
    """
    This function processes records from a DynamoDB stream.
    If a record has a 'FAIL' status, it sends a notification email via SES.
    """
    logger.info("🔔 Alert Lambda triggered")
    logger.debug("Received event: %s", json.dumps(event))

    # Safely get the list of records from the event payload.
    records = event.get("Records", [])
    for record in records:
        try:
            logger.info("📦 Processing record with eventID: %s", record.get('eventID'))

            # Ensure we only process new items inserted into the table
            if record.get("eventName") == "INSERT":
                # Use .get() for safe dictionary access
                new_image = record.get('dynamodb', {}).get('NewImage')
                if not new_image:
                    logger.warning("Record has no 'NewImage' field. Skipping.")
                    continue

                status = new_image.get('status', {}).get('S')
                logger.info("ℹ️ Invoice status: %s", status)

                if status == 'FAIL':
                    # Access fields, providing defaults if they are missing
                    invoice_id = new_image.get('invoice_id', {}).get('S', 'Unknown ID')
                    reason = new_image.get('reason', {}).get('S', 'No reason provided.')

                    email_body = f"Invoice validation failed for: {invoice_id}\n\nReason: {reason}"
                    logger.info("📧 Sending email for failed invoice %s...", invoice_id)

                    # Send the email notification
                    response = ses.send_email(
                        Source=os.environ['ALERT_EMAIL_FROM'],
                        Destination={'ToAddresses': [os.environ['ALERT_EMAIL_TO']]},
                        Message={
                            'Subject': {'Data': f'⚠️ Invoice Validation Failed: {invoice_id}'},
                            'Body': {'Text': {'Data': email_body}}
                        }
                    )
                    logger.info("✅ Email sent. SES Message ID: %s", response['MessageId'])
        except Exception as e:
            logger.error("❌ Error processing a record: %s", e, exc_info=True)
            continue

    return {'statusCode': 200, 'body': json.dumps('Alerts processed successfully.')}
