"""Gets the REAL list of sports (event categories) straight from BetDEX.

Docs: GET /categories (developers.betdex.com/api-reference/event-categories)

Run:
    python3 list_categories.py
"""
import sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
import requests
from api_client import BetdexClient

client = BetdexClient()
if not client.login():
    print("Login failed.")
    sys.exit(1)

resp = requests.get(f"{client.base_url}/categories", headers=client.headers)
if resp.status_code != 200:
    print(f"Request failed: {resp.status_code} {resp.text}")
    sys.exit(1)

data = resp.json()
categories = data.get("categories", [])
print(f"Total categories: {len(categories)}\n")
for c in categories:
    print(f"id={c.get('id'):<15} name={c.get('name')}")
