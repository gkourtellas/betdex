"""Keeps a permanent record of every bet in config/bets.db.
Used for reporting later — profit, win rate, etc.
"""

import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "bets.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name TEXT,
            event_name TEXT,
            league TEXT,
            selection_name TEXT,
            price REAL,
            stake REAL,
            step INTEGER,
            placed_at TEXT,
            settled_at TEXT,
            result TEXT,
            profit REAL
        )
    """)
    return conn


def record_bet_placed(strategy_name, event_name, selection_name, price, stake, step, placed_at, league=None):
    conn = _connect()
    cur = conn.execute(
        """INSERT INTO bets (strategy_name, event_name, league, selection_name, price, stake, step, placed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (strategy_name, event_name, league, selection_name, price, stake, step, placed_at.isoformat()),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def record_bet_settled(row_id, result, price, stake):
    """result: 'won' or 'lost'. Backing an outcome ('For'), so:
    win profit = stake * (price - 1), lose = -stake.
    """
    profit = round(stake * (price - 1), 4) if result == "won" else -stake
    conn = _connect()
    conn.execute(
        "UPDATE bets SET result = ?, profit = ?, settled_at = ? WHERE id = ?",
        (result, profit, datetime.utcnow().isoformat(), row_id),
    )
    conn.commit()
    conn.close()
