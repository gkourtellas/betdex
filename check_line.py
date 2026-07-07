import sys
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from datetime import datetime
from api_client import BetdexClient

client = BetdexClient()
client.login()
now = datetime.utcnow()
from_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
data = client.get_markets("FOOTBALL_OVER_UNDER_TOTAL_GOALS", from_iso)
markets = data.get("markets", [])
for m in markets[:10]:
    print(m.get("id"), m.get("marketValue"), m.get("lockAt"))
