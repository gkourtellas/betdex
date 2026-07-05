import sys, os
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from datetime import datetime
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

now = datetime.utcnow()
from_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

data = client.get_markets("FOOTBALL_FULL_TIME_RESULT", from_iso)
if not data or "markets" not in data:
    print("No data back from BetDEX.")
    sys.exit(1)

markets = data["markets"]
print(f"Total markets: {len(markets)}\n")

for m in markets:
    lock_at = m.get("lockAt")
    print(f"id={m.get('id')} suspended={m.get('suspended')} published={m.get('published')} "
          f"inPlayStatus={m.get('inPlayStatus')} lockAt={lock_at}")
