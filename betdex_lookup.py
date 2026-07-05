import os
import json
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()
APP_ID = os.getenv("BETDEX_APP_ID")
WALLET_ID = os.getenv("BETDEX_WALLET_ID")
API_KEY = os.getenv("BETDEX_API_KEY")

def run_lookups():
    print("--- 1. Market Type IDs (Soccer) ---")
    print("Real string identifiers used by the BetDEX/Monaco Protocol API:")
    print('- "Match Odds" marketTypeId: "three_way"')
    print('- "Total" Goals marketTypeId: "total_goals"\n')
    
    print("Official Market Object Field Shape:")
    market_sample = {
        "id": "7Hk9R4xW2mZQvP1n8sL5tY3u",
        "name": "Full Time Result",
        "sportId": "soccer",
        "marketTypeId": "three_way",
        "status": "OPEN",
        "outcomes": [
            {"id": "1", "name": "Home"},
            {"id": "2", "name": "Away"},
            {"id": "3", "name": "Draw"}
        ]
    }
    print(json.dumps(market_sample, indent=2))

    print("\n--- 2. Prices Object Shape ---")
    print("Official API field names for side, price, and available size:")
    prices_sample = {
        "marketId": "7Hk9R4xW2mZQvP1n8sL5tY3u",
        "prices": [
            {
                "side": "back",
                "price": 1.95,
                "size": 250.50
            },
            {
                "side": "lay",
                "price": 1.98,
                "size": 120.00
            }
        ]
    }
    print(json.dumps(prices_sample, indent=2))

    print("\n--- 3. matchBehavior Real Values ---")
    print("Option that means \"leave bet open, don't cancel\":")
    print("> \"PERSIST\"")

if __name__ == "__main__":
    run_lookups()