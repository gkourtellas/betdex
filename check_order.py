import sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

data = client.get_orders()
print(json.dumps(data, indent=2))
