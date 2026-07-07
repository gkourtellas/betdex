import sys
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, "src")
from datetime import datetime
from api_client import BetdexClient

client = BetdexClient()
client.login(notify=False)
print("Issued now:", datetime.utcnow())
print("Expires at:", client.access_expires_at)
