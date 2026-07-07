import sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from api_client import BetdexClient

client = BetdexClient()
client.login()

detail = client.get_market_by_id("840739")
outcomes = detail.get("marketOutcomes", [])
for o in outcomes:
    print(o.get("id"), repr(o.get("title")))

prices_data = client.get_market_prices("840739")
print(json.dumps(prices_data, indent=2))
