# Fraud Review Demo

A small Python prototype that analyzes transaction records and presents possible patterns for human review. It includes a FastAPI API, a SQLite database, a black-and-gold web dashboard, and CSV exports.

This is a learning and portfolio project. It uses synthetic sample data and is not a production fraud detection system. An alert is only a pattern that a person may review; it does not prove fraud.

## Features

- Reads transactions from the file transactions.csv.
- Checks whether a receiver gets transactions from at least three distinct senders within a 24-hour window.
- Writes alert results to alerts.csv and stores transactions and alerts in fraud_demo.db.
- Provides an API for listing transactions and alerts, and submitting transactions.
- Lets a reviewer mark an alert open, escalated, or cleared and record a note.
- Keeps review history and lets the reviewer download it as a CSV file.

## Requirements

- Windows with PowerShell
- Python 3.15
- The packages fastapi and uvicorn

## Setup

Open PowerShell in the project folder:

    cd C:\Users\User\OneDrive\Documents\fraud-network-demo

If the virtual environment has not been created, run:

    py -3.15 -m venv .venv

Install the web dependencies:

    .\.venv\Scripts\python.exe -m pip install fastapi uvicorn

## Run the transaction analysis

From the project folder, run:

    .\.venv\Scripts\python.exe .\src\main.py

The program reads transactions.csv, updates the SQLite database, and writes alerts.csv.

The input CSV must have these column names:

    sender,receiver,amount,timestamp

Use ISO-style timestamps such as 2026-11-01T09:00:00. The sample rows in the project are synthetic.

## Run the web dashboard

Start the API from the project folder:

    .\.venv\Scripts\python.exe -m uvicorn api:app --app-dir .\src --reload

Keep that PowerShell window open while using the app. Open the dashboard on the same computer at:

<http://127.0.0.1:8000/>

Interactive API documentation is available at:

<http://127.0.0.1:8000/docs>

## API routes

| Method | Route | Purpose |
| --- | --- | --- |
| GET | /health | Check that the API is running |
| GET | /alerts | List current review alerts |
| GET | /transactions | List recent transactions |
| POST | /transactions | Add a transaction |
| POST | /alerts/review | Save a review decision and note |
| GET | /alerts/review-history | View review history |
| GET | /alerts/review-history.csv | Download review history as CSV |

## Project files

    fraud-network-demo/
    ├── src/
    │   ├── api.py
    │   ├── dashboard.html
    │   └── main.py
    ├── transactions.csv
    ├── alerts.csv
    └── fraud_demo.db

transactions.csv is input data. alerts.csv and fraud_demo.db are generated or updated by the program.

## Data and security

Use synthetic data only. Do not put real customer, bank, or payment information into this demo.

The API currently runs on 127.0.0.1, which makes it available on the same computer. Do not change the host to 0.0.0.0 or publish this app online until proper authentication, access controls, and secure deployment settings have been added.
