import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "app.db"
print("DB:", DB_PATH.resolve())

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables before:", tables)

# Rename only if the wrong table exists
table_names = {t[0] for t in tables}
if "food logs" in table_names and "food_logs" not in table_names:
    cur.execute('ALTER TABLE "food logs" RENAME TO food_logs')
    conn.commit()
    print('Renamed "food logs" -> food_logs')
else:
    print("No rename needed (either already correct or both exist).")

tables_after = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables after:", tables_after)

conn.close()
