import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).parent
LEGACY_DB_PATH = BASE_DIR / "app.db"
APP_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "WhoopMealAI"
APP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APP_DIR / "app.db"


def _ensure_db_location() -> None:
    """
    Migrate the original repo-local database into a stable local app-data path
    the first time the app runs after switching locations.
    """
    if DB_PATH.exists():
        return
    if not LEGACY_DB_PATH.exists():
        return
    shutil.copy2(LEGACY_DB_PATH, DB_PATH)
    try:
        os.chmod(DB_PATH, 0o666)
    except OSError:
        pass

def get_conn() -> sqlite3.Connection:
    """
    Returns a SQLite connection using a fixed absolute path.
    """
    _ensure_db_location()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
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
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_food_logs_day
    ON food_logs(day);
    """)
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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS whoop_oauth_states (
        state TEXT PRIMARY KEY,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS import_status (
        import_name TEXT PRIMARY KEY,
        last_attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_succeeded_at TEXT,
        target_day TEXT,
        detected_day TEXT,
        source_path TEXT,
        source_kind TEXT,
        rows_found INTEGER NOT NULL DEFAULT 0,
        rows_inserted INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        message TEXT
    );
    """)
    conn.commit()

def init_db() -> None:
    """
    Creates tables if they do not exist.
    """
    print("Using database file:", DB_PATH.resolve())

    conn = get_conn()
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


def delete_food_logs_for_day(day: str, source: Optional[str] = None) -> int:
    conn = get_conn()
    cur = conn.cursor()
    if source:
        cur.execute("DELETE FROM food_logs WHERE day=? AND source=?", (day, source))
    else:
        cur.execute("DELETE FROM food_logs WHERE day=?", (day,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def replace_food_logs_for_day(day: str, rows: list[dict], source: str = "netdiary") -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM food_logs WHERE day=? AND source=?", (day, source))

    inserted = 0
    for row in rows:
        cur.execute(
            """
            INSERT INTO food_logs (source, eaten_at, day, item_name, calories, protein_g, carbs_g, fat_g)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("source", source),
                row.get("eaten_at"),
                row.get("day", day),
                row.get("item_name"),
                row.get("calories"),
                row.get("protein_g"),
                row.get("carbs_g"),
                row.get("fat_g"),
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def save_whoop_oauth_state(state: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO whoop_oauth_states (state)
        VALUES (?)
        """,
        (state,),
    )
    conn.commit()
    conn.close()


def delete_whoop_oauth_state(state: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM whoop_oauth_states WHERE state=?", (state,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def save_import_status(
    *,
    import_name: str,
    status: str,
    target_day: Optional[str],
    detected_day: Optional[str],
    source_path: Optional[str],
    source_kind: Optional[str],
    rows_found: int,
    rows_inserted: int,
    message: Optional[str],
    succeeded: bool,
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO import_status (
            import_name, last_attempted_at, last_succeeded_at, target_day, detected_day,
            source_path, source_kind, rows_found, rows_inserted, status, message
        )
        VALUES (
            ?, CURRENT_TIMESTAMP, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(import_name) DO UPDATE SET
            last_attempted_at=CURRENT_TIMESTAMP,
            last_succeeded_at=CASE WHEN excluded.last_succeeded_at IS NOT NULL THEN excluded.last_succeeded_at ELSE import_status.last_succeeded_at END,
            target_day=excluded.target_day,
            detected_day=excluded.detected_day,
            source_path=excluded.source_path,
            source_kind=excluded.source_kind,
            rows_found=excluded.rows_found,
            rows_inserted=excluded.rows_inserted,
            status=excluded.status,
            message=excluded.message
        """,
        (
            import_name,
            1 if succeeded else 0,
            target_day,
            detected_day,
            source_path,
            source_kind,
            rows_found,
            rows_inserted,
            status,
            message,
        ),
    )
    conn.commit()
    conn.close()


def get_import_status(import_name: str) -> Optional[dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute(
        """
        SELECT import_name, last_attempted_at, last_succeeded_at, target_day, detected_day,
               source_path, source_kind, rows_found, rows_inserted, status, message
        FROM import_status
        WHERE import_name=?
        """,
        (import_name,),
    ).fetchone()
    conn.close()

    if row is None:
        return None

    return {key: row[key] for key in row.keys()}

if __name__ == "__main__":
    init_db()

