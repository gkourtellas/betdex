"""Gets subcategories (the real per-sport breakdown) under each category.

Categories (from /categories) are just SPORTS / ESPORTS — too broad.
Subcategories under SPORTS should be the actual sport list (Football,
Tennis, etc). This calls /subcategories with the SPORTS category id.

If this 404s, the path/param name is wrong and needs adjusting — but
at least it will fail loudly instead of returning a guessed answer.

Run:
    python3 list_subcategories.py
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

# Try the most likely param name/shape first.
attempts = [
    ("/subcategories", {"categoryIds": "SPORTS"}),
    ("/subcategories", {"categoryId": "SPORTS"}),
    ("/subcategories", {}),
    ("/categories/SPORTS/subcategories", {}),
]

for path, params in attempts:
    url = f"{client.base_url}{path}"
    resp = requests.get(url, params=params, headers=client.headers)
    print(f"\nGET {path} params={params} -> {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        subs = data.get("subcategories", data)
        print(f"Success. Raw response:\n{resp.text[:3000]}")
        break
    else:
        print(resp.text[:300])
else:
    print("\nNone of the guessed paths worked. Need the exact path from BetDEX docs/support.")
