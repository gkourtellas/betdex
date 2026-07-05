import sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

order_id = "218584896"
url = f"{client.base_url}/orders/{order_id}"

import requests
response = requests.delete(url, headers=client.headers)
print(f"Status: {response.status_code}")
print(response.text)
