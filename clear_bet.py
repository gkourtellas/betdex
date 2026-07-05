import sys, json

STATE_FILE = "config/state/BD_Win_Step_6_1.5.json"

with open(STATE_FILE, encoding="utf-8") as f:
    state = json.load(f)

before = len(state.get("active_bets", []))
state["active_bets"] = [b for b in state.get("active_bets", []) if b.get("order_id") != "218584896"]
after = len(state["active_bets"])

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2)

print(f"Removed: {before - after} bet(s). Remaining: {after}")
