import sqlite3
from pathlib import Path
from typing import Any, Optional

# Always anchor DB to this file's directory
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "app.db"

def get_conn() -> sqlite3.Connection:
    """
    Returns a SQLite connection using a fixed absolute path.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """
    Creates tables if they do not exist.
    """
    print("Using database file:", DB_PATH.resolve())

    conn = get_conn()
    cur = conn.cursor()

    # Food logs table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS food_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL DEFAULT 'netdiary',
        eaten_at TEXT,
        day TEXT NOT NULL,
        item_name TEXT,
        calories REAL,
        protein_g REAL,
        carbs_g REAL,
        fat_g REAL
    );
    """)

    # Index for faster day queries
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_food_logs_day
    ON food_logs(day);
    """)

    # Lab results table for slower-changing metabolic context
    cur.execute("""
    CREATE TABLE IF NOT EXISTS lab_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL DEFAULT 'csv',
        collected_at TEXT,
        day TEXT NOT NULL,
        biomarker TEXT NOT NULL,
        value REAL,
        unit TEXT,
        notes TEXT
    );
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_lab_results_day
    ON lab_results(day);
    """)

    # WHOOP OAuth tokens for local development / single-user app usage
    cur.execute("""
    CREATE TABLE IF NOT EXISTS whoop_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        access_token TEXT NOT NULL,
        refresh_token TEXT,
        expires_in INTEGER,
        scope TEXT,
        token_type TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    conn.close()


def save_whoop_tokens(
    access_token: str,
    refresh_token: Optional[str],
    expires_in: Optional[int],
    scope: Optional[str],
    token_type: Optional[str],
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM whoop_tokens")
    cur.execute(
        """
        INSERT INTO whoop_tokens (access_token, refresh_token, expires_in, scope, token_type)
        VALUES (?, ?, ?, ?, ?)
        """,
        (access_token, refresh_token, expires_in, scope, token_type),
    )
    conn.commit()
    conn.close()


def get_whoop_tokens() -> Optional[dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute(
        """
        SELECT access_token, refresh_token, expires_in, scope, token_type, created_at
        FROM whoop_tokens
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()

    if row is None:
        return None

    return {
        "access_token": row["access_token"],
        "refresh_token": row["refresh_token"],
        "expires_in": row["expires_in"],
        "scope": row["scope"],
        "token_type": row["token_type"],
        "created_at": row["created_at"],
    }

if __name__ == "__main__":
    init_db()

