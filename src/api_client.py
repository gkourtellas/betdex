"""BetDEX REST API client and optional Telegram alerts.

Real order body confirmed working: needs a "side" field ("For" = back,
"Against" = lay) — API rejects the order with "side must not be null"
if it's missing. Unmatched stake behavior is controlled by matchBehavior:
  RetainUnmatched  -> stays open as a live offer (default, what we want)
  CancelUnmatched  -> gets cancelled if not matched right away

Docs: https://developers.betdex.com/
"""

import os
import uuid
import threading
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
        self.tg_chat_id_login = os.getenv("TELEGRAM_CHAT_ID_LOGIN", self.tg_chat_id)

        self.base_url = "https://prod.api.btdx.io"
        self.access_token = None
        self.access_expires_at = None
        self.headers = {"content-type": "application/json"}

        # Every strategy calls ensure_valid_session() on its own, each
        # from its own worker thread (via asyncio.to_thread). Without a
        # lock, several of them can notice the token is stale at the
        # same moment and each re-login separately — spamming Telegram
        # with duplicate "login successful" messages. This lock makes
        # only the first one actually log in; the rest just reuse the
        # fresh token once they get their turn.
        self._login_lock = threading.Lock()

    def login(self, notify=True):
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
                if notify:
                    self.send_telegram("✅ BetDEX login successful. Session token acquired.", chat_id=self.tg_chat_id_login)
                return True
            if notify:
                self.send_telegram(f"❌ BetDEX login failed. Status: {response.status_code}", chat_id=self.tg_chat_id_login)
            return False
        except Exception as e:
            if notify:
                self.send_telegram(f"❌ BetDEX login exception: {str(e)}", chat_id=self.tg_chat_id_login)
            return False

    def _token_needs_refresh(self):
        if not self.access_token or not self.access_expires_at:
            return True
        try:
            expires = datetime.strptime(self.access_expires_at, "%Y-%m-%dT%H:%M:%S.%fZ")
        except Exception:
            return True
        return datetime.utcnow() >= expires - timedelta(minutes=2)

    def ensure_valid_session(self):
        """Re-login if no token, or token close to its expiry.

        Locked + double-checked: if several strategies hit this at the
        same time, only the first one inside the lock actually logs in.
        The others wait for the lock, then see the token is fresh again
        and skip logging in — no duplicate Telegram messages.
        """
        if not self._token_needs_refresh():
            return True
        with self._login_lock:
            if not self._token_needs_refresh():
                return True
            return self.login()

    def get_markets(self, market_type_id, from_datetime, statuses="Open", published=True, size=100, page=0):
        """GET /markets — list markets of one type, filtered and paginated.
        Returns the raw response dict (has "markets", "_meta", etc) or None.
        """
        self.ensure_valid_session()
        url = f"{self.base_url}/markets"
        params = {
            "marketTypeIds": market_type_id,
            "fromDateTime": from_datetime,
            "statuses": statuses,
            "published": str(published).lower(),
            "size": size,
            "page": page,
            "sort": "lockAt,asc",
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
        """GET /markets/{id} — full market detail: outcomes, event, league, teams."""
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
        """GET /market-prices — price ladder for one market.
        Each entry in "prices" has: side ("For"/"Against"), outcomeId,
        price (odds), amount (size available at that price), matched.
        """
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

    def submit_order(self, market_id, outcome_id, price, stake, side="For",
                      keep_when_in_play=False, match_behavior="RetainUnmatched", reference=None):
        """POST /orders — place one order.

        side must be "For" (back) or "Against" (lay) — the API rejects
        the order with a 400 "side must not be null" error if this is
        left out. Backing an outcome = "For".

        match_behavior "RetainUnmatched" keeps any unmatched part of the
        stake open as a live offer instead of cancelling it — use this,
        not "CancelUnmatched", or your bets vanish immediately.
        """
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
            "reference": reference or str(uuid.uuid4()),
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
        """GET /orders — filter by orderIds or marketIds, scoped to this wallet.
        Response includes "orders", "markets" (with settledAt), and "trades"
        (with profitLoss) — use these three together to work out win/lose.
        """
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

    def send_telegram(self, message, chat_id=None):
        if not self.tg_token:
            return
        target = chat_id or self.tg_chat_id
        if not target:
            return
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {"chat_id": target, "text": message}
        try:
            requests.post(url, json=payload)
        except Exception:
            pass
