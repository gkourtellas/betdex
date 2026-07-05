import sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

detail = client.get_market_by_id("832112")
print(json.dumps(detail, indent=2))
