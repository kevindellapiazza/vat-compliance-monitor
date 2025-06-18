 ✅ VCM Invoice Validation Pipeline

A **real-time invoice validation pipeline** built on AWS using Textract, Lambda, SES, DynamoDB, and more.  
Designed to simulate real-world FinOps and compliance workflows using a **fully serverless architecture**.

---

## 🎯 Why I Built This

> “As a data scientist with could and AI skills, I wanted to simulate a real business scenario using modern AWS tools — combining OCR, compliance logic, real-time alerts, and serverless analytics.”

---

## 🧠 Project Overview

VCM (VAT Compliance Monitor) is a serverless system that:

- Ingests PDF invoices from suppliers (via S3)
- Uses Textract (OCR) to extract key data
- Validates VAT compliance (e.g., required fields, VAT rate matching)
- Stores results in DynamoDB and Parquet
- Sends real-time alerts via **Slack** (always), and **SES email** when validation fails
- Enables analytics and querying via Athena

---

## 🔧 Architecture & Technologies

### AWS Services Used

- **S3** — Stores uploaded invoice PDFs
- **Textract** — Extracts data from scanned PDFs
- **Lambda** — Runs parsing, validation, and alert logic
- **DynamoDB** — Stores validation results
- **Glue + Athena** — Transforms and queries Parquet data
- **SES + EventBridge** — Sends email alerts for failed invoices
- **Slack** — Real-time notification channel

---

## 🚀 How It Works

1. Upload an invoice PDF to **S3**
2. **S3 triggers a Lambda** → Textract extracts invoice data
3. The Lambda runs VAT compliance validation logic
4. Results are saved in **DynamoDB** and **Parquet**
5. A **Slack alert is always sent**  
6. If validation **fails**, an **email alert** is sent via **SES**

---

## 📂 Folder Structure

vat-compliance-monitor/
├── README.md
├── .gitignore
├── sam/ # SAM infrastructure (optional)
│ └── template.yaml # Blueprint for automated deployment
├── src/ # Lambda function code
│ ├── vcm-textract-lambda/ # Textract + validation logic
│ │ └── lambda_function.py
│ └── vcm-alert-lambda/ # SES email alerts
│ ├── lambda_function.py
│ └── requirements.txt
├── data/
│ ├── allowed-vat-rates.csv # VAT rule config
│ └── athena_output/.keep # Athena query output folder
├── docs/
│ └── .keep # Future diagrams, screenshots


---

## 📊 Key Features

- ✅ OCR and invoice parsing with Amazon Textract
- ✅ Compliance validation for required fields and VAT rules
- ✅ Real-time Slack alerts for every invoice processed
- ✅ SES email alerts for failed validations only
- ✅ DynamoDB + Parquet data storage
- ✅ Athena queries for historical analysis
- ✅ Optional SAM template for automated deployment


