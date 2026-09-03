"""Finds real market type ids matching a keyword, without guessing.

Scans ALL open markets (no marketTypeIds filter), stops as soon as it
sees an id containing the given keyword (case-insensitive).

Run:
    python3 get_market_names.py 'basketball'
    python3 get_market_names.py 'corners'
"""
import sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from datetime import datetime
import requests
from api_client import BetdexClient

if len(sys.argv) < 2:
    print("Usage: python3 get_market_names.py '<keyword>'")
    sys.exit(1)

keyword = sys.argv[1].strip().upper()

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

now = datetime.utcnow()
from_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

seen = set()
page = 0
while page < 400:
    params = {
        "fromDateTime": from_iso,
        "statuses": "Open",
        "published": "true",
        "size": 100,
        "page": page,
        "sort": "lockAt,asc",
    }
    resp = requests.get(f"{client.base_url}/markets", params=params, headers=client.headers)
    if resp.status_code != 200:
        print(f"Request failed on page {page}: {resp.status_code}")
        break

    markets = resp.json().get("markets", [])
    if not markets:
        break

    for m in markets:
        mt = m.get("marketType", {})
        ids = mt.get("_ids") or []
        if ids:
            seen.add(ids[0])

    matches = [s for s in seen if keyword in s]
    if matches:
        print(f"Found matching id(s): {matches}")
        sys.exit(0)

    page += 1

print(f"No id containing '{keyword}' found after {page} page(s).")
print(f"All {len(seen)} distinct id(s) seen: {sorted(seen)}")
