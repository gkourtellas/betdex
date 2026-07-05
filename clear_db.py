import sqlite3

conn = sqlite3.connect("config/bets.db")
cur = conn.execute(
    "DELETE FROM bets WHERE strategy_name = ? AND selection_name = ? AND result IS NULL",
    ("BD_Win_Step 6/1.5", "Nublense")
)
conn.commit()
print(f"Deleted rows: {cur.rowcount}")
conn.close()
