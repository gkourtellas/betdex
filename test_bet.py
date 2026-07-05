import sys, json
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
print(f"Total markets: {len(markets)}")

for m in markets:
    if m.get("suspended") or not m.get("published"):
        continue

    market_id = m["id"]
    prices_data = client.get_market_prices(market_id)
    if not prices_data or not prices_data.get("prices"):
        continue
    prices_entry = prices_data["prices"][0]
    price_list = prices_entry.get("prices", [])
    if not price_list:
        continue

    for p in price_list:
        if p.get("side") != "For":
            continue
        price = p.get("price")
        if price is None or not (1.30 <= price <= 2.50):
            continue

        detail = client.get_market_by_id(market_id)
        event_name = "?"
        for ev in (detail.get("events", []) if detail else []):
            event_name = ev.get("name", event_name)

        print(f"\nTrying real bet: market={market_id} event={event_name} "
              f"outcome={p.get('outcomeId')} price={price}")

        result = client.submit_order(
            market_id, p.get("outcomeId"), price, 0.10,
            keep_when_in_play=False,
            match_behavior="RetainUnmatched",
        )
        print("Order result:")
        print(json.dumps(result, indent=2) if result else "None (rejected, see error above)")
        sys.exit(0)

print("No matching market/price found to test with.")
