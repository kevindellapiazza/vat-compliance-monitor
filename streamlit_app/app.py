import streamlit as st
import boto3
import uuid
import os
import time
from botocore.exceptions import NoCredentialsError, ClientError

# ---------- CONFIG ----------
S3_BUCKET = "vcm-invoice-uploads-kevin"
REGION = "eu-central-1"
FOLDER_NAME = "invoices_uploaded_from_streamlit"

# AWS clients
s3 = boto3.client("s3", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table("vcm-invoice-status")

# ---------- PAGE UI ----------
st.set_page_config(page_title="VAT Compliance Monitor", page_icon="📄")
st.title("📤 Upload an Invoice for Validation")

st.markdown(
"""
    Upload a PDF invoice to simulate an end-to-end **VAT compliance validation pipeline**.

    Once uploaded, the system will:
    - Automatically extract key invoice data using OCR (Textract)
    - Run validation rules (e.g., required fields, VAT rate checks)
    - Return the result directly below

    ⚠️ **Note**: Please upload **one invoice at a time**.  
    To test another invoice, first delete the previous file from the S3 bucket to reset the system.
    """)

# ---------- FILE UPLOAD ----------
uploaded_file = st.file_uploader("Drag and drop a PDF invoice", type=["pdf"])

if uploaded_file is not None:
    st.success(f"✅ File selected: {uploaded_file.name}")
    
    if st.button("Upload to S3 and Start Validation"):
        with st.spinner("Uploading and triggering validation..."):
            try:
                base_filename = os.path.splitext(uploaded_file.name)[0]
                s3_key = f"{FOLDER_NAME}/{uploaded_file.name}"
                invoice_id = base_filename  # Match DynamoDB logic
                
                # Upload to S3
                s3.upload_fileobj(
                    uploaded_file,
                    S3_BUCKET,
                    s3_key
                )

                st.success("✅ Upload successful. Validation has been triggered.")

                # Wait and check DynamoDB for result
                with st.spinner("Waiting for validation result..."):
                    for _ in range(10):  # Try for ~10 seconds
                        time.sleep(1)
                        try:
                            response = table.get_item(Key={"invoice_id": invoice_id})
                            if "Item" in response:
                                result = response["Item"]
                                status = result["status"]
                                reason = result.get("reason", "All checks passed ✅")

                                if status == "PASS":
                                    st.success(f"✅ Validation Result: {status}")
                                else:
                                    st.error(f"❌ Validation Result: {status}")
                                
                                st.info(f"📄 Reason: {reason}")
                                break
                        except ClientError as e:
                            st.error("Error fetching validation result.")
                            break
                    else:
                        st.warning("⚠️ No validation result found yet. Please wait a few more seconds.")

            except NoCredentialsError:
                st.error("❌ AWS credentials not found.")
            except Exception as e:
                st.error(f"❌ Upload failed: {e}")
