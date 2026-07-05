import sys, json
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv()
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

market_id = "837233"
outcome_id = "1838872"

print("Test: side=For, price=1.50 (aggressive, should cross book if 'For' is the back side)")
result = client.submit_order(market_id, outcome_id, 1.50, 0.10, side="For")
print(json.dumps(result, indent=2) if result else "Rejected, see error above")
