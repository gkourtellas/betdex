"""BetDEX REST API client and optional Telegram alerts.

Docs: https://developers.betdex.com/
"""

import os
import requests
import json
from datetime import datetime, timedelta


class BetdexClient:
    def __init__(self):
        self.app_id = os.getenv("BETDEX_APP_ID")
        self.wallet_id = os.getenv("BETDEX_WALLET_ID")
        self.api_key = os.getenv("BETDEX_API_KEY")
        self.tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        self.base_url = "https://prod.api.btdx.io"
        self.access_token = None
        self.access_expires_at = None
        self.headers = {"content-type": "application/json"}

    def login(self):
        """Creates a session using appId, walletId and apiKey. Stores accessToken."""
        url = f"{self.base_url}/sessions"
        payload = {"appId": self.app_id, "walletId": self.wallet_id, "apiKey": self.api_key}
        try:
            response = requests.post(url, data=json.dumps(payload), headers=self.headers)
            if response.status_code == 200:
                session = response.json()["sessions"][0]
                self.access_token = session["accessToken"]
                self.access_expires_at = session["accessExpiresAt"]
                self.headers["authorization"] = "Bearer " + self.access_token
                self.send_telegram("✅ BetDEX login successful. Session token acquired.")
                return True
            self.send_telegram(f"❌ BetDEX login failed. Status: {response.status_code}")
            return False
        except Exception as e:
            self.send_telegram(f"❌ BetDEX login exception: {str(e)}")
            return False

    def ensure_valid_session(self):
        """Re-login if no token, or token close to its ~30 min expiry."""
        if not self.access_token or not self.access_expires_at:
            return self.login()
        try:
            expires = datetime.strptime(self.access_expires_at, "%Y-%m-%dT%H:%M:%S.%fZ")
        except Exception:
            return self.login()
        if datetime.utcnow() >= expires - timedelta(minutes=2):
            return self.login()
        return True

    def get_markets(self, market_type_ids, from_datetime, statuses="Open", published=True, size=100, page=0, sort="lockAt,asc"):
        """GET /markets — list markets, filtered and paginated."""
        self.ensure_valid_session()
        url = f"{self.base_url}/markets"
        params = {
            "marketTypeIds": market_type_ids,
            "fromDateTime": from_datetime,
            "statuses": statuses,
            "published": str(published).lower(),
            "size": size,
            "page": page,
            "sort": sort,
        }
        try:
            response = requests.get(url, params=params, headers=self.headers)
            if response.status_code == 401:
                if self.login():
                    response = requests.get(url, params=params, headers=self.headers)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Error fetching markets: {str(e)}")
            return None

    def get_market_by_id(self, market_id):
        """GET /markets/{id} — full market detail including outcome titles."""
        self.ensure_valid_session()
        url = f"{self.base_url}/markets/{market_id}"
        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code == 401:
                if self.login():
                    response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Error fetching market {market_id}: {str(e)}")
            return None

    def get_market_prices(self, market_id):
        """GET /market-prices — current back/lay price ladder for a market."""
        self.ensure_valid_session()
        url = f"{self.base_url}/market-prices"
        try:
            response = requests.get(url, params={"marketIds": market_id}, headers=self.headers)
            if response.status_code == 401:
                if self.login():
                    response = requests.get(url, params={"marketIds": market_id}, headers=self.headers)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Error fetching prices for market {market_id}: {str(e)}")
            return None

    def get_event(self, event_id):
        """GET /events — event, league, and team names for one event id."""
        self.ensure_valid_session()
        url = f"{self.base_url}/events"
        try:
            response = requests.get(url, params={"ids": event_id}, headers=self.headers)
            if response.status_code == 401:
                if self.login():
                    response = requests.get(url, params={"ids": event_id}, headers=self.headers)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Error fetching event {event_id}: {str(e)}")
            return None

    def submit_order(self, market_id, outcome_id, side, price, stake,
                      keep_when_in_play=False, match_behavior="CancelUnmatched"):
        """POST /orders — place a single order. side is 'For' or 'Against'."""
        self.ensure_valid_session()
        url = f"{self.base_url}/orders"
        payload = {
            "walletId": self.wallet_id,
            "marketId": str(market_id),
            "outcomeId": str(outcome_id),
            "side": side,
            "price": price,
            "stake": stake,
            "keepWhenInPlay": keep_when_in_play,
            "matchBehavior": match_behavior,
        }
        try:
            response = requests.post(url, data=json.dumps(payload), headers=self.headers)
            if response.status_code == 401:
                if self.login():
                    response = requests.post(url, data=json.dumps(payload), headers=self.headers)
            if response.status_code in (200, 201):
                return response.json()
            print(f"Order submission rejected. Status: {response.status_code}, Response: {response.text}")
            return None
        except Exception as e:
            print(f"Exception during order submission: {str(e)}")
            return None

    def get_orders(self, order_ids=None, market_ids=None):
        """GET /orders — filter by orderIds or marketIds. Always scoped to this wallet."""
        self.ensure_valid_session()
        url = f"{self.base_url}/orders"
        params = {"walletIds": self.wallet_id}
        if order_ids:
            params["ids"] = order_ids
        if market_ids:
            params["marketIds"] = market_ids
        try:
            response = requests.get(url, params=params, headers=self.headers)
            if response.status_code == 401:
                if self.login():
                    response = requests.get(url, params=params, headers=self.headers)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Error fetching orders: {str(e)}")
            return None

    def send_telegram(self, message):
        """Same Telegram alert helper as the matchbook bot."""
        if not self.tg_token or not self.tg_chat_id:
            return
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {"chat_id": self.tg_chat_id, "text": message}
        try:
            requests.post(url, json=payload)
        except Exception:
            pass
