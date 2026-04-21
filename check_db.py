import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "app.db"

print("DB file:", DB_PATH.resolve())

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

print("\nTables:")
print(cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall())

print("\nTotal rows in food_logs:")
print(cur.execute(
    "SELECT COUNT(1) FROM food_logs"
).fetchone())

print("\nDays present:")
print(cur.execute(
    "SELECT day, COUNT(1) FROM food_logs GROUP BY day ORDER BY day DESC"
).fetchall())

conn.close()
