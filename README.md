 # ✅ VCM Invoice Validation Pipeline

A **real-time invoice validation pipeline** built entirely on AWS using Textract, Lambda, SES, DynamoDB, EventBridge, and more.  
Designed to simulate real-world FinOps and compliance workflows using a **fully serverless architecture**.

---

## 🎯 Why I Built This

> “As a data scientist with cloud and AI skills, I wanted to simulate a real business scenario using modern AWS tools — combining OCR, compliance logic, real-time alerts, and serverless analytics.”

This project is 100% original and was built entirely by me, Kevin Della Piazza, to demonstrate full-stack cloud engineering and automation.

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

## 🌐 Interactive Preview

Test the full invoice compliance pipeline via this cloud-hosted Streamlit interface:  
🔗 **[Launch Validation App](https://vat-compliance-monitor-lfentssvkbaggt5qrfekkb.streamlit.app/)**

You can upload a sample invoice to trigger real-time processing, validation, and alerts.




---


## 🔧 Architecture & Technologies

AWS Services Used

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
├── sam/ # Optional SAM Infrastructure
│ └── template.yaml
├── src/ # Lambda functions
│ ├── vcm-textract-lambda/
│ └── vcm-alert-lambda/
├── data/
│ ├── allowed-vat-rates.csv
│ └── athena_output/
├── docs/ # Future screenshots / diagrams

---

## 📊 Key Features

- ✅ OCR and invoice parsing with Amazon Textract
- ✅ Compliance validation for required fields and VAT rules
- ✅ Real-time Slack alerts for every invoice processed
- ✅ SES email alerts for failed validations only
- ✅ DynamoDB + Parquet data storage
- ✅ Athena queries for historical analysis
- ✅ Optional SAM template for automated deployment

---

## 📦 Deployment Notes

This project was originally deployed manually using the AWS Console.  
The included `sam/template.yaml` file is a clean infrastructure blueprint that allows to redeploy using AWS SAM if desired.


---

## 🛡️ License & Use

This project is published for **educational and portfolio purposes only**.  
All code was written by Kevin Della Piazza.

You may:
- ✅ Read and learn from this project
- ✅ Ask to test it as part of a job application
- ❌ Not reuse the code in other portfolios, applications, or commercial tools

All rights reserved © Kevin Della Piazza 
