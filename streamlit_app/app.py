import streamlit as st
import boto3
import os
import time
from botocore.exceptions import NoCredentialsError, ClientError

# ---------- CONFIGURATION ----------
# Load configuration from environment variables (with sensible defaults for local dev)
# This allows the app to be configured in Streamlit Cloud Secrets.
S3_BUCKET = os.environ.get("S3_BUCKET", "vcm-kevin-pipeline-invoices")
REGION = os.environ.get("AWS_REGION", "eu-central-1")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "vcm-invoice-status-iac")

# Static configuration
UPLOAD_PREFIX = "raw"
SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_invoices")
POLLING_INTERVAL = 2  # Seconds to wait between DynamoDB checks
POLLING_TIMEOUT = 90  # Max seconds to wait for a result

# ---------- AWS CLIENTS ----------
# Initialize clients using the configured region
try:
    s3 = boto3.client("s3", region_name=REGION)
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
except Exception as e:
    st.error(f"Error initializing AWS clients: {e}. Check your AWS credentials and region.")
    st.stop()

# ---------- PAGE UI ----------
st.set_page_config(page_title="VAT Compliance Monitor", page_icon="📄")
st.title("📤 VCM — Invoice Upload & Validation")

st.markdown(
    """
    Upload a PDF invoice to trigger the end-to-end AWS serverless pipeline.
    The system will perform OCR, extract data, and run compliance checks in real-time.
    """
)

# ---------- FILE UPLOAD ----------
uploaded_file = st.file_uploader(
    "Upload a PDF Invoice",
    type=["pdf"],
    help="The file will be uploaded to S3 to trigger the pipeline.",
)

if uploaded_file is not None:
    st.success(f"File selected: `{uploaded_file.name}`")

    # Use the filename (without extension) as the unique invoice_id
    base_filename = os.path.splitext(uploaded_file.name)[0]
    s3_key = f"{UPLOAD_PREFIX}/{uploaded_file.name}"
    invoice_id = base_filename

    if st.button(f"🚀 Validate Invoice: `{invoice_id}`"):
        try:
            # --- 1. UPLOAD TO S3 ---
            with st.spinner(f"1/3: Uploading `{uploaded_file.name}` to S3..."):
                s3.upload_fileobj(uploaded_file, S3_BUCKET, s3_key)
            st.success("1/3: Upload complete. Pipeline triggered.")

            # --- 2. POLLING DYNAMODB ---
            spinner_text = (
                f"2/3: Waiting for validation result for `{invoice_id}`... "
                f"(Max {POLLING_TIMEOUT}s)"
            )
            with st.spinner(spinner_text):
                start_time = time.time()
                result = None
                while time.time() - start_time < POLLING_TIMEOUT:
                    try:
                        response = table.get_item(Key={"invoice_id": invoice_id})
                        if "Item" in response:
                            result = response["Item"]
                            break  # Found it!
                    except ClientError as e:
                        st.error(f"Error polling DynamoDB: {e}")
                        break
                    time.sleep(POLLING_INTERVAL)

                if not result:
                    warning_text = (
                        f"Validation timed out after {POLLING_TIMEOUT}s. "
                        "The pipeline may still be running."
                    )
                    st.warning(warning_text)
                    st.stop()

            st.success("2/3: Validation result received from DynamoDB.")

            # --- 3. DISPLAY RESULTS ---
            with st.spinner("3/3: Rendering results..."):
                status = result.get("status", "UNKNOWN")
                reason = result.get("reason", "No reason provided.")

                if status == "PASS":
                    st.balloons()
                    st.success(f"✅ Validation Result: {status}")
                else:
                    st.error(f"❌ Validation Result: {status}")

                st.info(f"ℹ️ Reason: {reason}")

                # Display extracted data in a clean way
                st.subheader("Extracted Data")
                col1, col2 = st.columns(2)

                vat_rate_text = (
                    f"{result.get('vat_rate', 0) * 100}%"
                    if result.get('vat_rate')
                    else "N/A"
                )
                vat_amount_text = (
                    f"{result.get('currency', '')} {result.get('vat_amount', 'N/A')}"
                )
                net_total_text = (
                    f"{result.get('currency', '')} {result.get('net_total', 'N/A')}"
                )

                col1.metric("Country", result.get("country", "N/A"))
                col2.metric("VAT ID", result.get("supplier_vat_id", "N/A"))
                col1.metric("VAT Rate", vat_rate_text)
                col2.metric("VAT Amount", vat_amount_text)
                col1.metric("Net Total", net_total_text)

                with st.expander("Show Full Result JSON"):
                    st.json(result, expanded=False)

        except NoCredentialsError:
            st.error("❌ AWS credentials not found. Configure credentials to run this app.")
        except ClientError as e:
            st.error(f"❌ AWS Client Error: {e.code} - {e.response['Error']['Message']}")
        except Exception as e:
            st.error(f"❌ An unexpected error occurred: {e}")

# ---------- SAMPLE INVOICES ----------
st.markdown("---")
st.subheader("📥 Download Sample Invoices")
st.caption("Don't have an invoice? Use one of these test cases.")

if os.path.exists(SAMPLE_DIR):
    sample_files = [f for f in os.listdir(SAMPLE_DIR) if f.endswith(".pdf")]

    if sample_files:
        cols = st.columns(len(sample_files))
        for idx, filename in enumerate(sample_files):
            with open(os.path.join(SAMPLE_DIR, filename), "rb") as f:
                with cols[idx]:
                    st.download_button(
                        label=filename,
                        data=f,
                        file_name=filename,
                        mime="application/pdf",
                        key=filename,
                    )
    else:
        st.warning("`sample_invoices/` directory is empty.")
else:
    st.warning("`sample_invoices/` directory not found.")
