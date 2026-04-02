"""SQLite-backed session history for call notes.

All data stays local — no AWS credentials needed for storage.
DB file lives alongside the app in call_notes_app/call_notes.db.
"""
import os
import sqlite3
import time
from datetime import datetime

TTL_DAYS = 60

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "call_notes.db")
_conn = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS call_notes_history (
                customer_name TEXT NOT NULL,
                timestamp     TEXT NOT NULL,
                transcript    TEXT,
                notes         TEXT,
                docx_path     TEXT,
                followup_email TEXT DEFAULT '',
                meddpicc_data TEXT DEFAULT '',
                expiry_ttl    INTEGER,
                PRIMARY KEY (customer_name, timestamp)
            )
        """)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_history_ts ON call_notes_history(timestamp)")
        # Migrate: add meddpicc_data column if missing (existing DBs)
        try:
            _conn.execute("SELECT meddpicc_data FROM call_notes_history LIMIT 1")
        except sqlite3.OperationalError:
            _conn.execute("ALTER TABLE call_notes_history ADD COLUMN meddpicc_data TEXT DEFAULT ''")
        _conn.commit()
    return _conn


def save_session(customer_name: str, transcript: str, notes: str, docx_path: str,
                 followup_email: str = "", meddpicc_data: str = ""):
    """Store a completed session locally in SQLite."""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO call_notes_history "
        "(customer_name, timestamp, transcript, notes, docx_path, followup_email, meddpicc_data, expiry_ttl) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (customer_name, datetime.now().isoformat(), transcript, notes, docx_path,
         followup_email, meddpicc_data, int(time.time()) + (TTL_DAYS * 86400)),
    )
    conn.commit()


def list_sessions(customer_name: str = None) -> list:
    """Return sessions, optionally filtered by customer name.

    Returns list of dicts sorted by timestamp descending.
    """
    conn = _get_conn()
    if customer_name:
        rows = conn.execute(
            "SELECT * FROM call_notes_history WHERE customer_name = ? ORDER BY timestamp DESC",
            (customer_name,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM call_notes_history ORDER BY timestamp DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_customers() -> list:
    """Return a sorted list of unique customer names."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DISTINCT customer_name FROM call_notes_history ORDER BY customer_name"
    ).fetchall()
    return [r["customer_name"] for r in rows]


def get_latest_meddpicc(customer_name: str) -> dict | None:
    """Return the most recent MEDDPICC data for a customer, or None if none exists.

    Used to seed coverage at the start of a new call so MEDDPICC builds cumulatively.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT meddpicc_data FROM call_notes_history "
        "WHERE customer_name = ? AND meddpicc_data != '' "
        "ORDER BY timestamp DESC LIMIT 1",
        (customer_name,),
    ).fetchone()
    if row and row["meddpicc_data"]:
        import json
        try:
            return json.loads(row["meddpicc_data"])
        except (json.JSONDecodeError, TypeError):
            return None
    return None
