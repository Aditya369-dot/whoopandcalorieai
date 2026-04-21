import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "app.db"
print("Using DB:", DB_PATH)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

day = "2026-02-05"

print("\nTotal rows for day:")
print(cur.execute("SELECT COUNT(1) FROM food_logs WHERE day=?", (day,)).fetchone())

print("\nSample rows:")
rows = cur.execute("""
SELECT item_name, calories, protein_g, carbs_g, fat_g, eaten_at
FROM food_logs
WHERE day=?
LIMIT 5
""", (day,)).fetchall()

for r in rows:
    print(r)

conn.close()
