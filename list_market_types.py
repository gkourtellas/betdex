"""Gets the REAL market types straight from BetDEX's own endpoint.
No guessing — this is GET /market-types, the official list.

Run:
    python3 list_market_types.py
"""
import sys
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
import requests
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

url = f"{client.base_url}/market-types"
resp = requests.get(url, headers=client.headers)
print(f"GET /market-types -> {resp.status_code}\n")

if resp.status_code == 200:
    print(resp.text)
else:
    print(resp.text[:500])
