"""Finds every sport + market type BetDEX currently has open, fast.

Faster than discover_market_types.py because:
  - hard page cap (default 15) instead of scanning forever
  - stops early once a few pages in a row bring back zero new market types
  - only fetches full market detail ONCE per market type (to read the
    sport/league name), not per market

Run:
    python3 discover_sports_and_markets.py
"""
import sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from datetime import datetime, timezone
import requests
from api_client import BetdexClient

MAX_PAGES = 15
STOP_AFTER_EMPTY_PAGES = 3  # stop if this many pages in a row add 0 new types

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

now = datetime.now(timezone.utc)
from_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

url = f"{client.base_url}/markets"
seen_types = {}   # market_type_id -> count
sample_market = {}  # market_type_id -> one raw market dict (for event lookup)
empty_streak = 0

for page in range(MAX_PAGES):
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
        print(f"page {page}: no markets, stopping.")
        break

    before = len(seen_types)
    for m in markets:
        raw = m.get("marketType") or {}
        ids = raw.get("_ids") or []
        mtid = ids[0] if ids else "UNKNOWN"
        seen_types[mtid] = seen_types.get(mtid, 0) + 1
        if mtid not in sample_market:
            sample_market[mtid] = m

    new_count = len(seen_types) - before
    print(f"page {page}: {len(markets)} market(s), +{new_count} new type(s), total types: {len(seen_types)}")

    empty_streak = empty_streak + 1 if new_count == 0 else 0
    if empty_streak >= STOP_AFTER_EMPTY_PAGES:
        print(f"No new market types for {STOP_AFTER_EMPTY_PAGES} pages in a row — stopping early.")
        break

print(f"\nFound {len(seen_types)} distinct market type(s). Looking up sport/event for each...\n")

# For each market type, fetch ONE market's detail to find its sport/league.
results = []  # (sport_or_league, market_type_id, count)
for mtid, count in seen_types.items():
    m = sample_market[mtid]
    detail = client.get_market_by_id(m["id"])
    sport = "UNKNOWN"
    league = "UNKNOWN"
    if detail:
        for eg in detail.get("eventGroups", []):
            league = eg.get("name", league)
        # sport is often on the event or eventGroup — check common fields
        for ev in detail.get("events", []):
            sport = ev.get("sportId") or ev.get("sport") or sport
        if sport == "UNKNOWN":
            for eg in detail.get("eventGroups", []):
                sport = eg.get("sportId") or eg.get("sport") or sport
    results.append((sport, league, mtid, count))

results.sort(key=lambda r: (r[0], r[2]))

print(f"{'SPORT':<15} {'LEAGUE':<25} {'MARKET TYPE ID':<45} COUNT")
print("-" * 95)
for sport, league, mtid, count in results:
    print(f"{sport:<15} {league:<25} {mtid:<45} {count}")

print("\nIf SPORT/LEAGUE show UNKNOWN, paste one full raw detail block below")
print("and the field names can be fixed:")
if results:
    first_mtid = results[0][2]
    print(json.dumps(client.get_market_by_id(sample_market[first_mtid]["id"]), indent=2)[:2000])
