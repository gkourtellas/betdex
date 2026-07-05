import sys
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

now = datetime.utcnow()
from_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

data = client.get_markets("FOOTBALL_FULL_TIME_RESULT", from_iso)
markets = data.get("markets", []) if data else []

athens = ZoneInfo("Europe/Athens")
utc = ZoneInfo("UTC")

rows = []
for m in markets:
    lock_at_str = m.get("lockAt")
    try:
        lock_at = datetime.strptime(lock_at_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=utc)
    except Exception:
        continue
    mins_left = (lock_at - now.replace(tzinfo=utc)).total_seconds() / 60
    if mins_left < 5 or mins_left > 180:
        continue
    rows.append((mins_left, m["id"], lock_at.astimezone(athens)))

rows.sort()

for mins_left, market_id, lock_athens in rows[:10]:
    detail = client.get_market_by_id(market_id)
    event_name = "?"
    for ev in (detail.get("events", []) if detail else []):
        event_name = ev.get("name", event_name)
    print(f"id={market_id} kickoff_athens={lock_athens.strftime('%Y-%m-%d %H:%M')} "
          f"mins_left={int(mins_left)} event={event_name}")
