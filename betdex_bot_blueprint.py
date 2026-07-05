import os
import json
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()
APP_ID = os.getenv("BETDEX_APP_ID")
WALLET_ID = os.getenv("BETDEX_WALLET_ID")
API_KEY = os.getenv("BETDEX_API_KEY")

def run_blueprint():
    print("--- 1. Sports Identifiers ---")
    sports_data = {
        "sports": [
            {"id": "soccer", "name": "Soccer"},
            {"id": "basketball", "name": "Basketball"},
            {"id": "football", "name": "American Football"}
        ]
    }
    print(json.dumps(sports_data, indent=2))

    print("\n--- 2. Events Object Structure ---")
    print("Fetched via GET /events?sportId=soccer:")
    event_sample = {
        "events": [
            {
                "id": "E_2x9M1a7K3vB5",
                "name": "Arsenal vs Chelsea",
                "sportId": "soccer",
                "startTime": "2026-07-15T19:45:00Z",
                "status": "UPCOMING"
            }
        ]
    }
    print(json.dumps(event_sample, indent=2))

    print("\n--- 3. Markets & Outcomes Blueprint ---")
    print("Fetched via GET /markets?eventId=E_2x9M1a7K3vB5:")
    markets_sample = {
        "markets": [
            {
                "id": "M_three_way_XYZ123",
                "eventId": "E_2x9M1a7K3vB5",
                "marketTypeId": "three_way",
                "name": "Full Time Result",
                "outcomes": [
                    {"id": "out_1", "name": "Home (Arsenal)"},
                    {"id": "out_2", "name": "Away (Chelsea)"},
                    {"id": "out_3", "name": "Draw"}
                ]
            },
            {
                "id": "M_total_goals_ABC456",
                "eventId": "E_2x9M1a7K3vB5",
                "marketTypeId": "total_goals",
                "name": "Total Goals 2.5",
                "outcomes": [
                    {"id": "out_over", "name": "Over 2.5"},
                    {"id": "out_under", "name": "Under 2.5"}
                ]
            }
        ]
    }
    print(json.dumps(markets_sample, indent=2))

    print("\n--- 4. Order Execution Blueprint (Missing Piece for Bot) ---")
    print("Payload format required to execute a bet via POST /orders:")
    order_payload = {
        "marketId": "M_three_way_XYZ123",
        "outcomeId": "out_1",
        "side": "back",
        "price": 1.95,
        "size": 10.00,
        "matchBehavior": "PERSIST"
    }
    print(json.dumps(order_payload, indent=2))

if __name__ == "__main__":
    run_blueprint()