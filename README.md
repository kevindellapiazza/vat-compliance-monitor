# ✅ VCM — VAT Compliance Monitor

[![Build Status](https://github.com/kevindellapiazza/vat-compliance-monitor/actions/workflows/build-test.yml/badge.svg)](https://github.com/kevindellapiazza/vat-compliance-monitor/actions)
[![Infrastructure as Code](https://img.shields.io/badge/IaC-AWS%20SAM-orange.svg)](https://aws.amazon.com/serverless/sam/)

> An end-to-end, serverless pipeline on AWS for real-time validation of European VAT invoices. Fully deployed and managed with Infrastructure as Code.

---

## 📌 Project Overview

**VCM (VAT Compliance Monitor)** is a production-grade, event-driven pipeline that automates the validation of European VAT invoices. The entire system is defined and deployed using **Infrastructure as Code (IaC)** with the AWS SAM Framework.

This project solves a critical business problem by transforming unstructured, real-world PDF invoices—**including low-quality scans**—into validated, auditable, and analytics-ready records, all automatically.

---

## 🌐 Interactive Preview

Test the full invoice compliance pipeline via the cloud-hosted Streamlit interface:
🔗 **[Launch Validation App](https://vat-compliance-monitor-lfentssvkbaggt5qrfekkb.streamlit.app/)** 📤

Upload a sample invoice to trigger real-time processing, validation, and alerts.

---

## 🗺️ System Architecture

This diagram shows how invoices flow through the system from upload to validation and analytics.

![Architecture Diagram](docs/VCMarchitecture.drawio.png)

### Medallion layers
The project follows the Medallion Architecture approach, structuring data into three distinct layers:

* **Bronze:** for storing raw, ingested data.
* **Silver:** for cleaned, processed, and transformed data.
* **Gold:** for business-ready datasets.

![Architecture Diagram](docs/VCM_medallion_architecture.drawio.png)


This layered design improves data quality, reliability, and usability. **[Full methodology here](https://github.com/kevindellapiazza/data-foundations-for-ai)**

---

## 🚀 How It Works (Step-by-Step)

1.  **Ingestion & Preprocessing:** A user uploads a PDF to the `raw/` prefix in the main **Invoice Bucket** (`vcm-kevin-pipeline-invoices`). This S3 event triggers a Docker-based **`preprocess-lambda`** that uses `ocrmypdf` to clean the file and apply a text layer, saving the result to the `processed/` prefix within the same bucket.

2.  **Extraction, Validation & Storage:** The new file in the `processed/` prefix triggers the **`textract-lambda`**. This function is the core of the pipeline and performs several actions:
    * It uses **Amazon Textract** for intelligent OCR.
    * It validates the extracted data against business rules.
    * It saves the `PASS`/`FAIL` result to **Amazon DynamoDB** for real-time status checks.
    * It saves the full, structured result to the separate **Analytics Bucket** (`vcm-config-kevin`) as a Parquet file.
    * It sends an operational update for every invoice to **Slack**.

3.  **Critical Alerting:** A `FAIL` status written to DynamoDB triggers the **`alert-lambda`** via a DynamoDB Stream. This function sends a detailed failure notification via **Amazon SES (email)**.

4.  **Serverless Analytics:** An **AWS Glue Crawler** catalogs the Parquet files from the Analytics Bucket, making them instantly queryable using standard SQL in **Amazon Athena**.

---

## ✨ Key Features & Architectural Highlights

1.  **100% Infrastructure as Code (IaC):** The entire cloud infrastructure—S3 buckets, DynamoDB tables, all three Lambda functions, IAM roles, and event triggers—is defined in a single `template.yaml` file. The whole system can be reliably deployed in any AWS account with a single `sam deploy` command.

2.  **Automated Preprocessing for "Dirty" PDFs:** The system solves the common problem of unreadable documents. A Docker-based Lambda function uses `ocrmypdf` to clean and apply a text layer to any incoming PDF, ensuring even scanned documents are machine-readable.

3.  **AI-Powered Data Extraction:** **AWS Textract** intelligently extracts structured data (VAT IDs, amounts, dates) from the cleaned PDFs, overcoming variations in invoice layouts.

4.  **Data Lake & Analytics Layer:** Processed data is saved in the optimized **Parquet** format. An **AWS Glue Crawler**, also defined as code, automatically catalogs this data, making it instantly queryable via standard SQL with **Amazon Athena**.

---

## 🎯 Why This Matters

-   💸 Businesses lose dozens of hours per month manually validating invoices for tax compliance.
-   ❌ Small VAT mismatches can lead to major penalties, audit failures, or rejected tax filings.
-   📉 Most companies still rely on spreadsheets and shared drives for compliance workflows.

VCM automates this process end-to-end—intelligently, scalably, and cost-effectively. As a data and cloud engineer, I built VCM to reflect the kind of automation modern finance teams need.

---

## ⚡ The Result

A system that is:

- ✅ **Smart Compliance** — flags errors in incorrectly filled invoices and confirms valid ones
- ✅ **Scalable** — can handle hundreds of invoices per day  
- ✅ **Cost-effective** — runs on AWS with near-zero infrastructure cost  
- ✅ **Fully automated** — no human intervention required  

---

## 💰 Cloud Cost Estimate (10,000 Invoices / Month)

This system is optimized for affordability, even at an enterprise scale.  
All costs are based on **eu-central-1 (Frankfurt)** region, using AWS's public pricing (as of June 2025).

### 🧮 Monthly Cost — 10,000 Invoices (Enterprise Usage)

| Service           | Approx. Cost | Description |
|------------------|--------------|-------------|
| **Textract OCR** | $15.00       | 1 page/invoice × 10,000 × $0.0015 |
| **Lambda compute** | $0.02      | 400 ms @ 256 MB → ~1,000 GB-s |
| **Lambda requests** | $0.002     | 10,000 × $0.20 per 1M requests |
| **Amazon SES**   | $1.00        | 10,000 emails @ $0.10 per 1K |
| **S3 Storage**   | ~$0.20       | PDFs + Parquet (~1 GB/month) |
| **Glue + Athena**| ~$1.00       | 1 crawler run + ~50 queries |
| **TOTAL**        | **~$18.25/month** | Fully serverless, 10k invoices processed monthly |

> 🔍 **Textract accounts for ~90% of total cost**. All other components combined cost < $5/month.

---

## 🔐 Security Best Practices (Deployed in eu-central-1)

| Layer     | Practice |
|-----------|----------|
| **S3**    | Server-side encryption (SSE-S3) enabled by default |
| **IAM**   | Each Lambda has least-privilege IAM roles (S3 + logging only) |
| **SES**   | Sandbox mode; verified sender & recipients only |
| **Parquet Output** | Stored in private S3 path, queryable only via Athena |
| **Monitoring** | CloudWatch logs + EventBridge alerts on failures |
| **No Public Access** | All resources use private IAM-authenticated triggers |

> ✅ Compliant with AWS’s Well-Architected security pillar — safe for real-world invoice data.

---

## 📦 Deployment

The entire infrastructure for this project is defined in the `sam/template.yaml` file and deployed as a single, cohesive stack using the AWS SAM Framework.

#### Prerequisites
-   AWS Account & IAM User
-   AWS CLI (configured with credentials)
-   AWS SAM CLI
-   Docker Desktop

#### Deployment Steps
1.  **Clone the repository:**

    ```bash
    git clone [https://github.com/kevindellapiazza/vat-compliance-monitor.git](https://github.com/kevindellapiazza/vat-compliance-monitor.git)
    cd vat-compliance-monitor
    ```
2.  **Build the application:**

    ```bash
    sam build -t sam/template.yaml
    ```
    This command builds the Docker image and packages the Lambda functions.
3.  **Deploy the stack:**

    ```bash
    sam deploy --guided
    ```
    This command starts a guided deployment. You will be prompted to enter parameters for your unique bucket name and secrets (emails, Slack URL).

---

## 🔧 Tools & Technologies

-   **IaC:** AWS SAM CLI
-   **Compute:** AWS Lambda (Python 3.12 Runtime & Docker Container Image)
-   **AI / OCR:** AWS Textract
-   **Storage:** Amazon S3, Amazon DynamoDB
-   **Data Analytics:** AWS Glue, Amazon Athena
-   **Alerting:** Amazon SES, Slack Webhooks
-   **CI/CD:** GitHub Actions
-   **Frontend:** Streamlit

---

## 🧠 Skills Demonstrated

-   **Infrastructure as Code (IaC):** Designed and deployed a complete, multi-resource cloud application from a single, reusable AWS SAM template.
-   **Serverless & Event-Driven Architecture:** Built a robust, scalable, and cost-efficient pipeline using S3 event triggers, Lambda functions, and DynamoDB Streams.
-   **AI/ML Integration:** Leveraged AWS Textract for intelligent document processing (OCR) to extract structured data from unstructured PDFs.
-   **Data Engineering:** Created a data pipeline that transforms and stores data in an analytics-optimized format (Parquet) and built a data catalog with AWS Glue for querying in Amazon Athena.
-   **CI/CD & DevOps:** Implemented a Continuous Integration workflow with GitHub Actions to automate code quality checks and testing.
-   **Cloud Security:** Applied the principle of least privilege with specific IAM roles for each service and managed secrets securely outside of version control using parameters.

---

## ✅ Continuous Integration (CI)

This project uses **GitHub Actions** to automatically run quality checks on every commit:
-   Check code quality with **Ruff**.
-   Run tests with **Pytest**.

---

## 💡 Future Improvements

-   **Implement Full CI/CD:** The current GitHub Action performs CI (testing). The next step is to add a CD (Continuous Deployment) stage that automatically runs `sam deploy` on every successful merge to the `main` branch.
-   **Replace Regex with Amazon Comprehend:** To make data extraction even more robust and language-agnostic, the Regex-based logic could be replaced with a custom Named Entity Recognition (NER) model trained using Amazon Comprehend.

---

## 📦 Configuration

Validation logic is driven by the file:  
`data/allowed-vat-rates.csv`

This file maps each country code to its allowed VAT rates and enables country-specific rule checks.  
Making this external (not hardcoded) ensures scalability and maintainability.

---

## 🛡️ License & Use

This project is published for educational and portfolio purposes only.  
All code was written by **Kevin Della Piazza**.

You may:
- ✅ Read and learn from this project  
- ✅ Ask to test it as part of a job application  

You may not:
- ❌ Reuse the code in other portfolios, applications, or commercial tools  

**All rights reserved © Kevin Della Piazza**
