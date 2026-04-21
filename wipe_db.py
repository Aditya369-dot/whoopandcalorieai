import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "app.db"
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("DELETE FROM food_logs;")
conn.commit()
conn.close()
print("Deleted all rows from food_logs.")
