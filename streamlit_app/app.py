import streamlit as st
import boto3
import time
import pandas as pd
import plotly.express as px
import base64
import os

# ---------- 1. CLOUD INFRASTRUCTURE SETUP ----------
# Initializing AWS clients using Streamlit Secrets for enhanced security.
try:
    aws_creds = {
        "aws_access_key_id": st.secrets["AWS_ACCESS_KEY_ID"],
        "aws_secret_access_key": st.secrets["AWS_SECRET_ACCESS_KEY"],
        "region_name": st.secrets["AWS_DEFAULT_REGION"]
    }
    # Boto3 clients for S3 (Storage) and DynamoDB (NoSQL Database)
    s3 = boto3.client("s3", **aws_creds)
    dynamodb = boto3.resource("dynamodb", **aws_creds)

    # Resource mapping from environment configuration
    S3_BUCKET = st.secrets["S3_BUCKET"]
    DYNAMODB_TABLE_NAME = st.secrets["DYNAMODB_TABLE"]
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
except Exception:
    st.error("Infrastructure Error: Verify your Streamlit Secrets.")
    st.stop()

# Operational Constants for the GenAI Pipeline
UPLOAD_PREFIX = "raw"
SAMPLE_DIR = "sample_invoices"
POLLING_INTERVAL = 2
POLLING_TIMEOUT = 90
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE_DIR, "sample_invoices")

# ---------- 2. SESSION MANAGEMENT ----------
if 'password_correct' not in st.session_state:
    st.session_state.password_correct = False

# ---------- 3. UI ENGINE ----------

def display_pdf_clean(file_path):
    """
    Encodes and embeds a PDF file using a Base64 string.
    Includes a discrete fallback expander with
    a download button for environments where browser
    security policies block embedded data URIs.
    """
    with open(file_path, "rb") as f:
        pdf_bytes = f.read()
        base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')

    # 1. Primary View: Embedded PDF using <embed> for better Chrome compatibility
    pdf_display = (
        f'<embed src="data:application/pdf;base64,{base64_pdf}" '
        f'width="100%" height="550" type="application/pdf" '
        f'style="border-radius: 10px;">'
    )
    st.markdown(pdf_display, unsafe_allow_html=True)

    # 2. Fallback Mechanism: Hidden expander to maintain clean UI aesthetics
    with st.expander("🛠️ Having trouble viewing the document?"):
        st.caption(
            "Some browsers (like Chrome) may block embedded previews for security. "
            "You can download the file to view it locally."
        )
        st.download_button(
            label="📥 Download PDF for Manual View",
            data=pdf_bytes,
            file_name=os.path.basename(file_path),
            mime="application/pdf",
            key=f"dl_{os.path.basename(file_path)}"
        )

def render_smart_extraction(item):
    """Renders structured AI extraction results and consistency checks."""
    status = item.get("status", "FAIL")

    if status == "PASS":
        st.success(f"**RESULT: {status}**")
    else:
        st.error(f"**RESULT: {status}**")
        st.warning(f"**Validation Logic Alert:** {item.get('reason', 'N/A')}")

    st.markdown("---")

    # Financial validation logic: ensuring Subtotal + VAT = Total
    curr = item.get('currency', '$')
    try:
        subtotal = float(item.get('net_total', 0))
        vat_amount = float(item.get('vat_amount', 0))
        grand_total = subtotal + vat_amount
        vat_rate_val = float(item.get("vat_rate", 0)) * 100
    except Exception:
        subtotal = item.get('net_total', 'N/A')
        vat_amount = item.get('vat_amount', 'N/A')
        grand_total = 'N/A'
        vat_rate_val = "N/A"

    # Row 1: Supplier Identity Data
    c1, c2 = st.columns(2)
    c1.metric("Origin Country", item.get("country", "N/A"))
    c2.metric("Supplier VAT ID", item.get("supplier_vat_id", "N/A"))

    st.markdown("<br>", unsafe_allow_html=True)

    # Row 2: Tax and Subtotal breakdown
    c3, c4, c5 = st.columns(3)
    c3.metric("Subtotal", f"{curr} {subtotal}")
    c4.metric(
        "VAT Rate",
        f"{vat_rate_val:.1f}%" if isinstance(vat_rate_val, float) else "N/A"
    )
    c5.metric("VAT Amount", f"{curr} {vat_amount}")

    st.markdown("<br>", unsafe_allow_html=True)

    # Row 3: Final Computed Total
    total_label = (
        f"{curr} {grand_total:.2f}" if isinstance(grand_total, float)
        else f"{curr} {grand_total}"
    )
    st.metric("Total (Gross)", total_label)

    # Technical Deep Dive: Showing raw JSON output
    with st.expander("📝 View Full Extraction Data (JSON)", expanded=False):
        st.json(item)

# ---------- 4. DASHBOARD PAGES ----------

def show_demo_page():
    """Showcases a pre-analyzed document to demonstrate AI accuracy."""
    st.title("✨ Smart GenAI Data Extraction")
    st.write(
        "Transition from unstructured invoice documents to"
        " validated data with **GenAI**."
    )

    st.markdown("""
    **Intelligence Checks:**
    - ✅ **VAT ID Integrity**: format validation for EU suppliers.
    - ✅ **Regulatory Compliance**: VAT rates match origin country rules.
    - ✅ **Mathematical Consistency**: *Subtotal + VAT Amount = Total Gross*.
    """)

    # Fetching a known good example from the DynamoDB analytical store
    demo_id = "Factura_test_1"
    pdf_path = os.path.join(SAMPLE_DIR, "INV-1004.pdf")

    res = table.get_item(Key={"invoice_id": demo_id})
    if "Item" in res:
        item = res["Item"]
        st.subheader("📄 1. Ingestion: Original Document")
        display_pdf_clean(pdf_path)
        st.markdown("---")
        st.subheader("✨ 2. Smart Extraction Results")
        render_smart_extraction(item)
    else:
        st.warning("Demo record missing from DynamoDB.")

def show_analytics_page():
    """Visualizes operational health using Plotly."""
    st.title("📊 Analytics Dashboard")
    st.caption("""
        This dashboard delivers real-time operational insights.
        For advanced analytics, all datasets are stored in **Amazon S3** and are
        accessible via SQL through **Amazon Athena**.
        This architecture supports a scalable **data lakehouse** approach.
    """)

    try:
        # Scanning DynamoDB for aggregated visualization
        response = table.scan()
        items = response.get('Items', [])
        if items:
            df = pd.DataFrame(items)
            k1, k2, k3 = st.columns(3)
            k1.metric("Total Ingested", len(df))
            pass_rate = (df['status'] == 'PASS').mean() * 100
            k2.metric("Pass Rate", f"{pass_rate:.1f}%")
            k3.metric("Regions Covered", df['country'].nunique())

            st.divider()
            c1, c2 = st.columns(2)
            with c1:
                st.plotly_chart(
                    px.pie(
                        df, names='status', color='status',
                        color_discrete_map={'PASS': '#2ca02c', 'FAIL': '#d62728'},
                        title="Pipeline Status"
                    ),
                    use_container_width=True
                )
            with c2:
                # Vertical bar chart for geographic distribution
                cnt_counts = df['country'].value_counts().reset_index()
                st.plotly_chart(
                    px.bar(
                        cnt_counts, x='count', y='country',
                        orientation='h', title="Regional Volume"
                    ),
                    use_container_width=True
                )
    except Exception as e:
        st.error(f"Analytics Unavailable: {e}")

def show_live_test():
    """Triggers the full asynchronous event-driven AWS pipeline."""
    st.title("🚀 Live Test Pipeline")

    # Cost management gate to prevent automated script abuse
    if not st.session_state.password_correct:
        st.info("""
            🔒 **Secure Access Required**: Live AWS resources (Textract and
            Bedrock) are protected to manage costs and prevent abuse.
            **Message me on LinkedIn for the demo password!**
        """)
        pwd = st.text_input("Demo Key:", type="password")
        if st.button("Unlock Sandbox"):
            if pwd == st.secrets["password"]:
                st.session_state.password_correct = True
                st.rerun()
            else:
                st.error("Invalid key.")
        st.stop()

    st.success("🔓 **System Online**: AWS Production Environment Active")

    # Utility to let recruiters test with sample documents
    with st.expander("📂 Download Sample Invoices"):
        files = [f for f in os.listdir(SAMPLE_DIR) if f.endswith(".pdf")]
        cols = st.columns(len(files))
        for i, f_name in enumerate(files):
            with open(os.path.join(SAMPLE_DIR, f_name), "rb") as f:
                cols[i].download_button(f"📄 {f_name}", f, file_name=f_name)

    uploaded_file = st.file_uploader("Upload PDF", type=["pdf"])
    if uploaded_file and st.button("🔍 Execute Extraction"):
        with st.spinner("AI Processing..."):
            try:
                # 1. Ingesting file to S3 triggers the backend
                s3.upload_fileobj(
                    uploaded_file, S3_BUCKET, f"{UPLOAD_PREFIX}/{uploaded_file.name}"
                )
                invoice_id = os.path.splitext(uploaded_file.name)[0]

                # 2. Polling DynamoDB to retrieve the result
                start_time = time.time()
                while time.time() - start_time < POLLING_TIMEOUT:
                    response = table.get_item(Key={"invoice_id": invoice_id})
                    if "Item" in response:
                        st.success("Complete!")
                        render_smart_extraction(response["Item"])
                        break
                    time.sleep(POLLING_INTERVAL)
            except Exception as e:
                st.error(f"Error: {e}")

# ---------- 5. NAVIGATION ----------
st.sidebar.title("VCM Monitor")
view = st.sidebar.radio(
    "Navigation",
    ["Instant Demo", "Analytics Dashboard", "Live Pipeline Test"]
)

if view == "Instant Demo":
    show_demo_page()
elif view == "Analytics Dashboard":
    show_analytics_page()
else:
    show_live_test()

# Security Divider and Lock Button to reset session state
if st.session_state.password_correct:
    st.sidebar.divider()
    if st.sidebar.button("Lock Sandbox"):
        st.session_state.password_correct = False
        st.rerun()
