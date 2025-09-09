import streamlit as st
import boto3
import os
import time
from botocore.exceptions import NoCredentialsError, ClientError

# ---------- CONFIG ----------
S3_BUCKET = "vcm-kevin-pipeline-invoices"
REGION = "eu-central-1"
UPLOAD_PREFIX = "raw"
SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_invoices")

# AWS clients
s3 = boto3.client("s3", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table("vcm-invoice-status-iac")

# ---------- PAGE UI ----------
st.set_page_config(page_title="VAT Compliance Monitor", page_icon="📄")
st.title("📤 Upload an Invoice")

st.markdown(
    """
This demo showcases a cloud-native **VAT document analyzer**, built on a serverless
**AWS architecture** — solving key problems in financial processing:
**⏱️ Time delays** and **❌ costly manual errors**.

---

### 💡 What You Can Try in This Demo

- Upload a single invoice (PDF only) from a supported country: IT, DE, FR, ES, BE, CH.
- Or download one of the 5 sample invoices at the end of the page
- Instantly see validation results

⚠️ **Note:** This demo supports one invoice at a time.
**Please delete the previous upload before submitting a new one.**

---

### ✨ Behind the Scenes

1. 📤 Saved to **Amazon S3** → `raw/` folder
2. ⚙️ Triggered by **Lambda Preprocessing**
3. ✨ The **Preprocessing Lambda** saves the new, text-layered PDF
   to Amazon S3 → `processed/` folder.
4. 🔍 Text extracted by **Textract**
5. 🧾 Results saved in **DynamoDB**
6. 🔔 Alerts via **Slack + Email**
7. 📊 Query-ready in **Athena**

✅ This system processes invoices automatically — no overlap, no ambiguity.
"""
)

# ---------- FILE UPLOAD ----------
uploaded_file = st.file_uploader(
    "📎 Upload a PDF Invoice (Recommended: < 5 MB)",
    type=["pdf"],
    help="For best performance, upload small invoices under 5 MB.",
)

if uploaded_file is not None:
    st.success(f"✅ File selected: {uploaded_file.name}")

    if st.button("Upload to S3 and Start Validation"):
        with st.spinner("Uploading and triggering validation..."):
            try:
                base_filename = os.path.splitext(uploaded_file.name)[0]
                s3_key = f"{UPLOAD_PREFIX}/{uploaded_file.name}"
                invoice_id = base_filename

                s3.upload_fileobj(uploaded_file, S3_BUCKET, s3_key)
                st.success("✅ Upload successful. Validation has been triggered.")

                with st.spinner("Waiting for validation result..."):
                    for _ in range(60):
                        time.sleep(1.5)
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
                        except ClientError:
                            st.error("Error fetching validation result.")
                            break
                    else:
                        st.warning(
                            "⚠️ No validation result found yet. "
                            "Please wait a few more seconds."
                        )

            except NoCredentialsError:
                st.error("❌ AWS credentials not found.")
            except Exception as e:
                st.error(f"❌ Upload failed: {e}")

# ---------- SAMPLE INVOICES ----------
st.markdown(
    "<br><br><br><br><br><hr style='height:4px;border:none;background-color:#bbb;'><br>",
    unsafe_allow_html=True,
)

st.subheader("📥 Download Sample Invoices")
st.caption("Use one of these 5 ready-to-test invoices:")

if os.path.exists(SAMPLE_DIR):
    sample_files = [f for f in os.listdir(SAMPLE_DIR) if f.endswith(".pdf")]

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
    st.warning("No sample invoices found in the `sample_invoices/` folder.")
