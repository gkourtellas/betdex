"""Collects real league names per sport from live BetDEX markets.

Long-term collection: not all leagues have matches on any given day,
so this is meant to run repeatedly (see run_periodically) over weeks/
months, merging into config/leagues_seen.json each time so the list
only grows.

Doesn't guess market type ids — scans ALL open markets (generic
/markets call, no marketTypeIds filter, same as discover_market_types.py),
buckets each event into a sport by its marketType id prefix, then
fetches ONE market detail per unique event to read the real league
name (eventGroups).
"""
import asyncio
import os
import json
from datetime import datetime
import requests

OUT_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "leagues_seen.json")

SPORT_PREFIXES = [
    "FOOTBALL", "BASKETBALL", "TENNIS", "ICE_HOCKEY",
    "AMERICAN_FOOTBALL", "BASEBALL",
]


def _sport_for(market_type_id):
    for prefix in SPORT_PREFIXES:
        if market_type_id.startswith(prefix):
            return prefix
    return "OTHER"


def _collect_once(client):
    client.ensure_valid_session()

    now = datetime.utcnow()
    from_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    event_sport = {}
    event_sample_market = {}

    page = 0
    while True:
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
            print(f"[league_collector] Request failed on page {page}: {resp.status_code}")
            break
        markets = resp.json().get("markets", [])
        if not markets:
            break

        for m in markets:
            mt = m.get("marketType", {})
            ids = mt.get("_ids") or []
            mtid = ids[0] if ids else "UNKNOWN"

            ev = m.get("event", {})
            eids = ev.get("_ids") or []
            if not eids:
                continue
            event_id = eids[0]

            event_sport.setdefault(event_id, _sport_for(mtid))
            event_sample_market.setdefault(event_id, m["id"])

        page += 1

    print(f"[league_collector] Scanned {page} page(s), {len(event_sample_market)} distinct events. Fetching league names...")

    found = {}
    for event_id, market_id in event_sample_market.items():
        detail = client.get_market_by_id(market_id)
        if not detail:
            continue
        league_name = None
        for eg in detail.get("eventGroups", []):
            league_name = eg.get("name")
        if not league_name:
            continue
        sport = event_sport[event_id]
        found.setdefault(sport, set()).add(league_name)

    existing = {}
    if os.path.isfile(OUT_FILE):
        with open(OUT_FILE, encoding="utf-8") as f:
            existing = json.load(f)

    added_total = 0
    for sport, leagues in found.items():
        before = set(existing.get(sport, []))
        merged = before | leagues
        added_total += len(merged - before)
        existing[sport] = sorted(merged)

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    total = sum(len(v) for v in existing.values())
    print(f"[league_collector] Run done. +{added_total} new league(s). Total tracked: {total}.")


async def run_periodically(client, interval_hours=24):
    """Runs forever inside the bot process, once per interval. First
    run happens immediately on startup, then every interval_hours.
    """
    while True:
        try:
            await asyncio.to_thread(_collect_once, client)
        except Exception as e:
            print(f"[league_collector] ⚠️ Error: {e}")
        await asyncio.sleep(interval_hours * 3600)
