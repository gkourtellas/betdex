"""Finds the REAL market type IDs BetDEX uses, instead of guessing.

We've been assuming names like FOOTBALL_CORNERS_OVER_UNDER without ever
confirming them against the API — that's why Corners has scanned 0
markets for a week. This script asks BetDEX for markets with NO type
filter, then prints every distinct marketTypeId it actually returns,
so we can see the real name for corners (and anything else close to it).

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

# Pull markets with a wide lookahead and NO marketTypeIds filter, across
# a few pages, to build up a real list of type IDs that exist right now.
url = f"{client.base_url}/markets"
seen_types = {}
page = 0
while page < 10:  # cap pages, just scanning for distinct type IDs
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

    if page == 0 and markets:
        print("Raw shape of first market object (so we stop guessing field names):")
        print(json.dumps(markets[0], indent=2))
        print()

    for m in markets:
        raw = m.get("marketTypeId") or m.get("marketType") or "UNKNOWN_FIELD"
        # marketTypeId may be a dict like {"id": "...", "name": "..."} rather
        # than a plain string — handle both instead of crashing.
        if isinstance(raw, dict):
            mtid = raw.get("id") or raw.get("name") or json.dumps(raw)
        else:
            mtid = raw
        seen_types[mtid] = seen_types.get(mtid, 0) + 1

    page += 1

print("\nDistinct market type IDs seen:")
for mtid, count in sorted(seen_types.items(), key=lambda x: -x[1]):
    flag = "  <-- possible corners match" if "CORNER" in str(mtid).upper() else ""
    print(f"  {mtid}: {count} market(s){flag}")

if not any("CORNER" in str(k).upper() for k in seen_types):
    print("\nNo corners-like market type found in this window. Corners markets "
          "may be less frequent — try increasing lookahead, or check BetDEX's "
          "docs/support for the exact ID they use for corners.")
