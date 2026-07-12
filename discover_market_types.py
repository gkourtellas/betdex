"""Finds the REAL market type IDs BetDEX uses, instead of guessing.

Run:
    python discover_market_types.py
"""
import sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from datetime import datetime, timedelta
import requests
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

now = datetime.utcnow()
from_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

url = f"{client.base_url}/markets"
seen_types = {}
page = 0
while True:  # no page cap — scan until BetDEX stops returning markets
    params = {
        "fromDateTime": from_iso,
        "statuses": "Open",
        "published": "true",
        "size": 100,
        "page": page,
        "sort": "lockAt,asc",
    }
    resp = requests.get(url, params=params, headers=client.headers)
    if resp.status_code != 200:
        print(f"Request failed on page {page}: {resp.status_code} {resp.text}")
        break

    data = resp.json()
    markets = data.get("markets", [])
    if not markets:
        break

    if page == 0:
        print("Raw shape of first market object (so we stop guessing field names):")
        print(json.dumps(markets[0], indent=2))
        print()

    for m in markets:
        raw = m.get("marketTypeId") or m.get("marketType") or "UNKNOWN_FIELD"
        # Real shape is {"_ref": "marketTypes.id", "_ids": ["SOME_ID"]}.
        if isinstance(raw, dict):
            ids = raw.get("_ids") or []
            mtid = ids[0] if ids else (raw.get("id") or raw.get("name") or json.dumps(raw))
        else:
            mtid = raw
        seen_types[mtid] = seen_types.get(mtid, 0) + 1

    print(f"page {page}: {len(markets)} market(s), running total distinct types: {len(seen_types)}")
    page += 1

print("\nDistinct market type IDs seen:")
for mtid, count in sorted(seen_types.items(), key=lambda x: -x[1]):
    flag = "  <-- possible corners match" if "CORNER" in str(mtid).upper() else ""
    print(f"  {mtid}: {count} market(s){flag}")

if not any("CORNER" in str(k).upper() for k in seen_types):
    print("\nNo corners-like market type found. Corners markets may not currently be "
          "offered as a product on BetDEX, or none are open right now.")