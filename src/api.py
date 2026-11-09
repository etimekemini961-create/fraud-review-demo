import csv
import hashlib
import io
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from main import find_alerts, save_alerts_csv

project_folder = Path(__file__).resolve().parent.parent
database_path = project_folder / "fraud_demo.db"
alerts_csv_path = project_folder / "alerts.csv"
dashboard_path = Path(__file__).resolve().parent / "dashboard.html"

app = FastAPI(title="Fraud Review Demo API")

def alert_key_for(receiver: str, window_start: str, window_end: str) -> str:
    value = f"{receiver}|{window_start}|{window_end}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ensure_review_tables(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS alert_reviews (
            alert_key TEXT PRIMARY KEY,
            receiver TEXT NOT NULL DEFAULT '',
            window_start TEXT NOT NULL DEFAULT '',
            window_end TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS alert_review_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key TEXT NOT NULL DEFAULT '',
            receiver TEXT NOT NULL DEFAULT '',
            window_start TEXT NOT NULL DEFAULT '',
            window_end TEXT NOT NULL DEFAULT '',
            previous_status TEXT NOT NULL DEFAULT 'open',
            status TEXT NOT NULL DEFAULT 'open',
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
    """)

    required_columns = {
        "alert_reviews": {
            "receiver": "TEXT NOT NULL DEFAULT ''",
            "window_start": "TEXT NOT NULL DEFAULT ''",
            "window_end": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'open'",
            "note": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        },
        "alert_review_history": {
            "alert_key": "TEXT NOT NULL DEFAULT ''",
            "receiver": "TEXT NOT NULL DEFAULT ''",
            "window_start": "TEXT NOT NULL DEFAULT ''",
            "window_end": "TEXT NOT NULL DEFAULT ''",
            "previous_status": "TEXT NOT NULL DEFAULT 'open'",
            "status": "TEXT NOT NULL DEFAULT 'open'",
            "note": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        },
    }

    for table_name, columns in required_columns.items():
        present = {
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
        }
        for column_name, definition in columns.items():
            if column_name not in present:
                connection.execute(
                    f'ALTER TABLE "{table_name}" '
                    f'ADD COLUMN "{column_name}" {definition}'
                )

@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(dashboard_path)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/alerts")
def list_alerts():
    if not database_path.exists():
        raise HTTPException(status_code=503, detail="Database not found. Run main.py first.")

    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            ensure_review_tables(connection)
            rows = connection.execute("""
                SELECT receiver, sender_count, sender_list, total_amount,
                       window_hours, window_start, window_end
                FROM alerts
                ORDER BY receiver, window_start
            """).fetchall()

            result = []
            for row in rows:
                item = dict(row)
                key = alert_key_for(
                    item["receiver"], item["window_start"], item["window_end"]
                )
                review = connection.execute(
                    "SELECT status, note FROM alert_reviews WHERE alert_key = ?",
                    (key,),
                ).fetchone()
                item["review_status"] = review["status"] if review else "open"
                item["review_note"] = review["note"] if review else ""
                result.append(item)

        return result
    except sqlite3.OperationalError as error:
        raise HTTPException(status_code=503, detail=f"Database tables are not ready: {error}")


@app.get("/transactions")
def list_transactions():
    if not database_path.exists():
        raise HTTPException(status_code=503, detail="Database not found. Run main.py first.")

    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("""
                SELECT id, sender, receiver, amount, timestamp
                FROM transactions
                ORDER BY id DESC
                LIMIT 100
            """).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError as error:
        raise HTTPException(status_code=503, detail=f"Database tables are not ready: {error}")


@app.get("/alerts/review-history")
def list_review_history():
    if not database_path.exists():
        raise HTTPException(status_code=503, detail="Database not found. Run main.py first.")

    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            ensure_review_tables(connection)
            rows = connection.execute("""
                SELECT id, alert_key, receiver, window_start, window_end,
                       previous_status, status, note, updated_at
                FROM alert_review_history
                ORDER BY id DESC
                LIMIT 200
            """).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError as error:
        raise HTTPException(status_code=503, detail=f"Database tables are not ready: {error}")



@app.get("/alerts/review-history.csv")
def download_review_history_csv():
    if not database_path.exists():
        raise HTTPException(status_code=503, detail="Database not found. Run main.py first.")

    try:
        with sqlite3.connect(database_path) as connection:
            ensure_review_tables(connection)
            rows = connection.execute("""
                SELECT id, receiver, window_start, window_end,
                       previous_status, status, note, updated_at
                FROM alert_review_history
                ORDER BY id DESC
            """).fetchall()
    except sqlite3.OperationalError as error:
        raise HTTPException(status_code=503, detail=f"Database tables are not ready: {error}")

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "id", "receiver", "window_start", "window_end",
        "previous_status", "status", "note", "updated_at",
    ])
    writer.writerows(rows)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="review-history.csv"'},
    )
class TransactionInput(BaseModel):
    sender: str = Field(min_length=1, max_length=80)
    receiver: str = Field(min_length=1, max_length=80)
    amount: float = Field(gt=0)
    timestamp: datetime


class AlertReviewInput(BaseModel):
    receiver: str = Field(min_length=1, max_length=80)
    window_start: datetime
    window_end: datetime
    status: Literal["open", "escalated", "cleared"]
    note: str = Field(default="", max_length=1000)


def refresh_alerts(connection: sqlite3.Connection):
    rows = connection.execute("""
        SELECT sender, receiver, amount, timestamp
        FROM transactions
    """).fetchall()

    transactions_by_receiver = defaultdict(list)
    for sender, receiver, amount, timestamp_text in rows:
        transactions_by_receiver[receiver].append({
            "sender": sender,
            "amount": amount,
            "timestamp": datetime.fromisoformat(timestamp_text),
        })

    alerts = find_alerts(transactions_by_receiver)
    connection.execute("DELETE FROM alerts")
    connection.executemany("""
        INSERT INTO alerts (
            receiver, sender_count, sender_list, total_amount,
            window_hours, window_start, window_end
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            alert["receiver"], alert["sender_count"], alert["sender_list"],
            alert["total_amount"], alert["window_hours"],
            alert["window_start"], alert["window_end"],
        )
        for alert in alerts
    ])
    return alerts


@app.post("/transactions", status_code=201)
def add_transaction(transaction: TransactionInput):
    sender = transaction.sender.strip()
    receiver = transaction.receiver.strip()

    if not sender or not receiver:
        raise HTTPException(status_code=422, detail="Sender and receiver cannot be blank.")
    if transaction.timestamp.tzinfo is not None:
        raise HTTPException(
            status_code=422,
            detail="For this demo, send a timestamp without a timezone offset.",
        )
    if not database_path.exists():
        raise HTTPException(status_code=503, detail="Database not found. Run main.py first.")

    try:
        with sqlite3.connect(database_path) as connection:
            cursor = connection.execute("""
                INSERT INTO transactions (sender, receiver, amount, timestamp)
                VALUES (?, ?, ?, ?)
            """, (sender, receiver, transaction.amount, transaction.timestamp.isoformat()))
            transaction_id = cursor.lastrowid
            alerts = refresh_alerts(connection)
    except sqlite3.OperationalError as error:
        raise HTTPException(status_code=503, detail=f"Database tables are not ready: {error}")

    save_alerts_csv(alerts, alerts_csv_path)
    return {
        "message": "Transaction saved and alerts recalculated.",
        "transaction_id": transaction_id,
        "alert_count": len(alerts),
    }


@app.post("/alerts/review")
def save_alert_review(review: AlertReviewInput):
    receiver = review.receiver.strip()
    window_start = review.window_start.isoformat()
    window_end = review.window_end.isoformat()

    if not receiver:
        raise HTTPException(status_code=422, detail="Receiver cannot be blank.")
    if review.window_start.tzinfo is not None or review.window_end.tzinfo is not None:
        raise HTTPException(
            status_code=422,
            detail="For this demo, send window timestamps without timezone offsets.",
        )
    if not database_path.exists():
        raise HTTPException(status_code=503, detail="Database not found. Run main.py first.")

    key = alert_key_for(receiver, window_start, window_end)
    now = datetime.now(timezone.utc).isoformat()
    note = review.note.strip()

    try:
        with sqlite3.connect(database_path) as connection:
            ensure_review_tables(connection)
            current_alert = connection.execute("""
                SELECT 1 FROM alerts
                WHERE receiver = ? AND window_start = ? AND window_end = ?
            """, (receiver, window_start, window_end)).fetchone()

            if current_alert is None:
                raise HTTPException(
                    status_code=404,
                    detail="This alert is no longer current. Refresh the dashboard.",
                )

            previous = connection.execute(
                "SELECT status FROM alert_reviews WHERE alert_key = ?", (key,)
            ).fetchone()
            previous_status = previous[0] if previous else "open"

            connection.execute("""
                INSERT INTO alert_reviews (
                    alert_key, receiver, window_start, window_end, status, note, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alert_key) DO UPDATE SET
                    status = excluded.status,
                    note = excluded.note,
                    updated_at = excluded.updated_at
            """, (key, receiver, window_start, window_end, review.status, note, now))

            connection.execute("""
                INSERT INTO alert_review_history (
                    alert_key, receiver, window_start, window_end,
                    previous_status, status, note, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key, receiver, window_start, window_end,
                previous_status, review.status, note, now,
            ))
    except sqlite3.OperationalError as error:
        raise HTTPException(status_code=503, detail=f"Database tables are not ready: {error}")

    return {
        "message": "Review saved.",
        "receiver": receiver,
        "status": review.status,
        "alert_key": key,
    }





