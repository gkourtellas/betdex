"""Saves and loads progress for each strategy separately.

Each strategy gets its own small file in config/state/, named after the
strategy. This way one strategy's progress never overwrites another's.
"""

import json
import os
import re
from datetime import datetime

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "config", "state")


def _safe_filename(name):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return f"{cleaned}.json"


def _path_for(name):
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, _safe_filename(name))


def load_state(name):
    """Returns (current_step, active_bets list, balance) for one strategy.
    balance is None for normal ladder strategies. Compound strategies use
    it to carry their running balance across restarts.
    """
    path = _path_for(name)
    if not os.path.isfile(path):
        return 1, [], None
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        current_step = state.get("current_step", 1)
        active_bets = state.get("active_bets", [])
        balance = state.get("balance")
        for bet in active_bets:
            bet["lock_at"] = datetime.fromisoformat(bet["lock_at"])
            bet["placed_at"] = datetime.fromisoformat(bet["placed_at"])
        return current_step, active_bets, balance
    except Exception as e:
        print(f"[{name}] ⚠️ Could not read saved progress ({e}). Starting fresh.")
        return 1, [], None


def save_state(name, current_step, active_bets, balance=None):
    path = _path_for(name)
    try:
        to_save = {
            "current_step": current_step,
            "active_bets": [
                {**bet, "lock_at": bet["lock_at"].isoformat(), "placed_at": bet["placed_at"].isoformat()}
                for bet in active_bets
            ],
            "balance": balance,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
    except Exception as e:
        print(f"[{name}] ⚠️ Could not save progress: {e}")
