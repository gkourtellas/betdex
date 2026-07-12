import sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

ids = ["860821","860883","861633","861700","861748","861856","861799",
       "861911","861913","862118","862156","862214","856927"]

for market_id in ids:
    data = client.get_market_prices(market_id)
    if not data or not data.get("prices"):
        continue
    entry = data["prices"][0]
    if entry.get("prices"):
        print(f"market {market_id} has an open book:")
        print(json.dumps(entry, indent=2))
        break
else:
    print("None of these had an open book.")