import sys
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from datetime import datetime, timedelta
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

now = datetime.utcnow()
from_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

data = client.get_markets("FOOTBALL_FULL_TIME_RESULT", from_iso)
markets = data.get("markets", []) if data else []

for m in markets:
    lock_at_str = m.get("lockAt")
    try:
        lock_at = datetime.strptime(lock_at_str, "%Y-%m-%dT%H:%M:%S.%fZ")
    except Exception:
        continue
    if lock_at - now > timedelta(hours=3):
        continue

    detail = client.get_market_by_id(m["id"])
    event_name = "?"
    for ev in (detail.get("events", []) if detail else []):
        event_name = ev.get("name", event_name)
    print(f"id={m['id']} lockAt={lock_at_str} event={event_name}")
