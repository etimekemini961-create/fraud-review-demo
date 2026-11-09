import argparse
import csv
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

WINDOW_HOURS = 24
MIN_SENDERS = 3

ALERT_FIELDS = [
    "receiver",
    "sender_count",
    "sender_list",
    "total_amount",
    "window_hours",
    "window_start",
    "window_end",
]


def load_transactions(csv_path):
    transactions_by_receiver = defaultdict(list)
    errors = []

    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)

            required_columns = {"sender", "receiver", "amount", "timestamp"}
            actual_columns = set(reader.fieldnames or [])
            missing_columns = required_columns - actual_columns

            if missing_columns:
                raise SystemExit(
                    "CSV is missing required columns: "
                    + ", ".join(sorted(missing_columns))
                )

            for row_number, row in enumerate(reader, start=2):
                try:
                    sender = row["sender"].strip()
                    receiver = row["receiver"].strip()
                    amount = float(row["amount"])
                    timestamp = datetime.fromisoformat(row["timestamp"].strip())

                    if not sender or not receiver:
                        raise ValueError("sender and receiver cannot be blank")
                    if amount <= 0:
                        raise ValueError("amount must be greater than zero")

                    transactions_by_receiver[receiver].append({
                        "sender": sender,
                        "amount": amount,
                        "timestamp": timestamp,
                    })

                except (AttributeError, ValueError) as error:
                    errors.append(f"Row {row_number}: {error}")

    except FileNotFoundError:
        raise SystemExit(f"Input file not found: {csv_path}")

    return transactions_by_receiver, errors


def find_alerts(transactions_by_receiver):
    candidates = []
    seen_windows = set()
    window = timedelta(hours=WINDOW_HOURS)

    for receiver, transactions in transactions_by_receiver.items():
        transactions.sort(key=lambda tx: tx["timestamp"])

        for start_tx in transactions:
            window_start = start_tx["timestamp"]
            window_end = window_start + window

            matching = [
                tx for tx in transactions
                if window_start <= tx["timestamp"] <= window_end
            ]
            unique_senders = sorted({tx["sender"] for tx in matching})

            signature = (
                receiver,
                tuple(
                    (tx["sender"], tx["amount"], tx["timestamp"])
                    for tx in matching
                ),
            )

            if len(unique_senders) >= MIN_SENDERS and signature not in seen_windows:
                seen_windows.add(signature)
                total = sum(tx["amount"] for tx in matching)

                candidates.append({
                    "receiver": receiver,
                    "sender_count": len(unique_senders),
                    "sender_list": ";".join(unique_senders),
                    "total_amount": f"{total:.2f}",
                    "window_hours": WINDOW_HOURS,
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                })

    filtered_alerts = []

    for candidate in candidates:
        candidate_senders = set(candidate["sender_list"].split(";"))
        candidate_start = datetime.fromisoformat(candidate["window_start"])
        candidate_end = datetime.fromisoformat(candidate["window_end"])
        is_redundant = False

        for other in candidates:
            if other is candidate or other["receiver"] != candidate["receiver"]:
                continue

            other_senders = set(other["sender_list"].split(";"))
            other_start = datetime.fromisoformat(other["window_start"])
            other_end = datetime.fromisoformat(other["window_end"])

            windows_overlap = (
                candidate_start <= other_end
                and other_start <= candidate_end
            )
            candidate_is_subset = candidate_senders <= other_senders
            other_is_stronger = len(other_senders) > len(candidate_senders)
            same_group_starts_earlier = (
                candidate_senders == other_senders
                and other_start < candidate_start
            )

            if (
                windows_overlap
                and candidate_is_subset
                and (other_is_stronger or same_group_starts_earlier)
            ):
                is_redundant = True
                break

        if not is_redundant:
            filtered_alerts.append(candidate)

    return filtered_alerts


def save_to_database(new_transactions, database_path):
    with sqlite3.connect(database_path) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY,
                sender TEXT NOT NULL,
                receiver TEXT NOT NULL,
                amount REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY,
                receiver TEXT NOT NULL,
                sender_count INTEGER NOT NULL,
                sender_list TEXT NOT NULL,
                total_amount TEXT NOT NULL,
                window_hours INTEGER NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL
            )
        """)

        for receiver, transactions in new_transactions.items():
            for transaction in transactions:
                sender = transaction["sender"]
                amount = transaction["amount"]
                timestamp = transaction["timestamp"].isoformat()

                connection.execute(
                    """
                    INSERT INTO transactions (sender, receiver, amount, timestamp)
                    SELECT ?, ?, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM transactions
                        WHERE sender = ?
                          AND receiver = ?
                          AND amount = ?
                          AND timestamp = ?
                    )
                    """,
                    (
                        sender,
                        receiver,
                        amount,
                        timestamp,
                        sender,
                        receiver,
                        amount,
                        timestamp,
                    ),
                )

        rows = connection.execute("""
            SELECT sender, receiver, amount, timestamp
            FROM transactions
        """).fetchall()

        all_transactions = defaultdict(list)

        for sender, receiver, amount, timestamp_text in rows:
            all_transactions[receiver].append({
                "sender": sender,
                "amount": amount,
                "timestamp": datetime.fromisoformat(timestamp_text),
            })

        alerts = find_alerts(all_transactions)

        connection.execute("DELETE FROM alerts")
        connection.executemany(
            """
            INSERT INTO alerts (
                receiver, sender_count, sender_list, total_amount,
                window_hours, window_start, window_end
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    alert["receiver"],
                    alert["sender_count"],
                    alert["sender_list"],
                    alert["total_amount"],
                    alert["window_hours"],
                    alert["window_start"],
                    alert["window_end"],
                )
                for alert in alerts
            ],
        )

    return all_transactions, alerts


def save_alerts_csv(alerts, output_path):
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=ALERT_FIELDS)
        writer.writeheader()
        writer.writerows(alerts)


def main():
    project_folder = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(
        description="Check transactions for review patterns."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=project_folder / "transactions.csv",
        help="Path to the transaction CSV file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_folder / "alerts.csv",
        help="Path where the alert CSV will be saved",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=project_folder / "fraud_demo.db",
        help="Path to the SQLite database file",
    )
    args = parser.parse_args()

    csv_transactions, errors = load_transactions(args.input)
    all_transactions, alerts = save_to_database(
        csv_transactions,
        args.database,
    )
    save_alerts_csv(alerts, args.output)

    transaction_count = sum(
        len(transactions)
        for transactions in all_transactions.values()
    )

    print(f"Database now contains {transaction_count} transaction(s).")

    for alert in alerts:
        print(
            f"Review {alert['receiver']}: {alert['sender_count']} different "
            f"senders within {alert['window_hours']} hours, "
            f"total {alert['total_amount']}"
        )

    print(f"Saved {len(alerts)} alert(s) to {args.output}")
    print(f"Stored transactions and alerts in {args.database}")

    for error in errors:
        print(f"Input warning: {error}")


if __name__ == "__main__":
    main()