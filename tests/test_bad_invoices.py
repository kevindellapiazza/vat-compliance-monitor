import os

def test_bad_invoice_exists():
    files = os.listdir("tests/assets")
    bad_pdfs = [f for f in files if f.endswith(".pdf")]
    assert len(bad_pdfs) >= 2, "🚨 Add at least 2 bad invoice PDFs to tests/assets/"
