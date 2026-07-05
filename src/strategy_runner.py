"""Runs one strategy's scan -> bet -> wait -> settle -> repeat loop.

Each strategy gets one of these, running on its own, side by side with
every other strategy. They don't affect each other.

Supports:
- normal ladder strategies (staking_plan steps up on loss, resets on win)
- compound strategies (stake = whole balance, balance compounds on win,
  strategy disables itself on loss or when target balance is reached)
- multi_market strategies (scan more than one market_type_id per pass)
- live_mode: "pre" (pre-match only), "live" (in-play only), "both"

Does NOT include cash-out or lay-side betting yet.
"""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import market_match_odds
import market_total
from state_store import load_state, save_state
from bet_records import record_bet_placed, record_bet_settled
from strategy_loader import disable_strategy

OVER_UNDER_HINT = "OVER_UNDER"


def _matcher_for(market_type_id):
    return market_total if OVER_UNDER_HINT in market_type_id else market_match_odds


class StrategyRunner:
    def __init__(self, strategy, client):
        self.cfg = strategy
        self.name = strategy["name"]
        self.client = client

        self.strategy_type = strategy.get("strategy_type", "normal")
        self.strategy_mode = strategy.get("strategy_mode", "single")

        if self.strategy_mode == "multi_market":
            self.market_configs = strategy["market_configs"]
        else:
            self.market_configs = [strategy]

        if self.strategy_type == "compound":
            self.compound_start = float(strategy["compound_start"])
            self.compound_target = float(strategy["compound_target"])
            self.staking_plan = None
            self.max_steps = 1
        else:
            self.staking_plan = strategy["staking_plan"]
            self.max_steps = len(self.staking_plan)

        self.current_step, self.active_bets, saved_balance = load_state(self.name)
        if self.strategy_type == "compound":
            self.balance = saved_balance if saved_balance is not None else self.compound_start

        self.max_open_bets = strategy.get("max_open_bets", 1)
        self.poll_interval = strategy.get("poll_interval_seconds", 600)
        self.cooldown_after_bet = strategy.get("open_positions_cooldown_seconds", 600)
        self.lookahead_minutes = strategy.get("event_lookahead_minutes", 180)
        self.min_seconds_to_start = strategy.get("min_seconds_to_start", 300)
        self.live_mode = strategy.get("live_mode", "pre")  # pre / live / both

    def log(self, msg):
        ts = datetime.now(ZoneInfo("Europe/Athens")).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [{self.name}] {msg}")

    def _save(self):
        balance = self.balance if self.strategy_type == "compound" else None
        save_state(self.name, self.current_step, self.active_bets, balance)

    def stake_for_step(self):
        if self.strategy_type == "compound":
            return round(self.balance, 2)
        idx = max(0, min(self.current_step - 1, len(self.staking_plan) - 1))
        return float(self.staking_plan[idx])

    def _market_passes_live_filter(self, market):
        is_live = market.get("inPlayStatus") == "InPlay"
        if self.live_mode == "pre":
            return not is_live
        if self.live_mode == "live":
            return is_live
        return True  # both

    async def run(self):
        self.log(f"Starting. Configs: {len(self.market_configs)}, mode: {self.strategy_mode}, "
                  f"type: {self.strategy_type}")
        while True:
            try:
                if len(self.active_bets) < self.max_open_bets:
                    await self.scan_and_bet()

                await self.check_settlements()

            except Exception as e:
                self.log(f"⚠️ Error in loop: {e}")

            await asyncio.sleep(self.poll_interval if not self.active_bets else 30)

    async def scan_and_bet(self):
        now = datetime.utcnow()
        horizon = now + timedelta(minutes=self.lookahead_minutes)
        from_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        for market_cfg in self.market_configs:
            market_type_id = market_cfg["market_type_id"]
            matcher = _matcher_for(market_type_id)
            is_total = matcher is market_total

            data = await asyncio.to_thread(self.client.get_markets, market_type_id, from_iso)
            if not data or "markets" not in data:
                self.log(f"Scanned {market_type_id}: no data back from BetDEX.")
                continue

            markets = data["markets"]
            self.log(f"Scanning {len(markets)} market(s) for {market_type_id}...")

            for market in markets:
                if market.get("suspended") or not market.get("published"):
                    continue

                if is_total and not market_total.market_matches_line(market, market_cfg):
                    continue

                if not self._market_passes_live_filter(market):
                    continue

                lock_at_str = market.get("lockAt")
                if not lock_at_str:
                    continue
                try:
                    lock_at = datetime.strptime(lock_at_str, "%Y-%m-%dT%H:%M:%S.%fZ")
                except Exception:
                    continue

                is_live = market.get("inPlayStatus") == "InPlay"
                if not is_live:
                    if lock_at <= now or lock_at > horizon:
                        continue
                    if (lock_at - now).total_seconds() < self.min_seconds_to_start:
                        continue

                event_id = (market.get("event") or {}).get("_ids", [None])[0]
                already_bet = any(b.get("event_id") == event_id for b in self.active_bets)
                if already_bet:
                    continue

                market_id = market["id"]

                prices_data = await asyncio.to_thread(self.client.get_market_prices, market_id)
                if not prices_data or not prices_data.get("prices"):
                    continue
                prices_entry = prices_data["prices"][0]
                if not prices_entry.get("prices"):
                    continue

                detail = await asyncio.to_thread(self.client.get_market_by_id, market_id)
                if not detail:
                    continue
                market_detail = detail.get("markets", [{}])[0]

                # market_detail["marketOutcomes"] is only a ref pointer
                # ({"_ref":..,"_ids":[...]}) — the real outcome objects
                # (id, title) live in the top-level detail["marketOutcomes"].
                outcome_ids = (market_detail.get("marketOutcomes") or {}).get("_ids", [])
                outcomes_by_id = {o["id"]: o for o in detail.get("marketOutcomes", [])}
                outcomes = [outcomes_by_id[oid] for oid in outcome_ids if oid in outcomes_by_id]

                found = matcher.find_opportunity(outcomes, prices_entry, market_cfg)
                if not found:
                    continue

                outcome_id, outcome_title, price = found
                stake = self.stake_for_step()

                event_name = "Unknown Match"
                league_name = "Unknown League"
                for ev in detail.get("events", []):
                    event_name = ev.get("name", event_name)
                for eg in detail.get("eventGroups", []):
                    league_name = eg.get("name", league_name)

                self.log(f"🎯 Match found: {event_name} -> {outcome_title} @ {price}")

                order_result = await asyncio.to_thread(
                    self.client.submit_order,
                    market_id, outcome_id, price, stake,
                    keep_when_in_play=(self.live_mode != "pre"),
                    match_behavior="RetainUnmatched",
                )

                if not order_result:
                    self.log("⚠️ Bet was rejected.")
                    continue

                orders = order_result.get("orders", [])
                placed_order = orders[0] if orders else {}

                bet = {
                    "order_id": placed_order.get("id"),
                    "market_id": market_id,
                    "event_id": event_id,
                    "lock_at": lock_at,
                    "placed_at": datetime.utcnow(),
                    "selection_name": outcome_title,
                    "event_name": event_name,
                    "league": league_name,
                    "stake": stake,
                    "price": price,
                    "step": self.current_step,
                }
                bet["record_id"] = record_bet_placed(
                    self.name, event_name, outcome_title, price, stake,
                    self.current_step, bet["placed_at"], league=league_name
                )
                self.active_bets.append(bet)
                self._save()

                msg = (
                    f"🚀 Bet Placed [{self.name}]\n"
                    f"Step: {self.current_step}/{self.max_steps}\n"
                    f"League: {league_name}\n"
                    f"Match: {event_name}\n"
                    f"Selection: {outcome_title}\n"
                    f"Price: {price}\n"
                    f"Stake: {stake}"
                )
                self.log(msg)
                self.client.send_telegram(msg)
                return  # one new bet per scan pass

        self.log("Scan done: nothing matched the strategy right now.")

    async def check_settlements(self):
        if not self.active_bets:
            return

        still_open = []
        for bet in self.active_bets:
            resume_time = bet["lock_at"] + timedelta(seconds=self.cooldown_after_bet)
            now = datetime.utcnow()

            if now < resume_time:
                still_open.append(bet)
                continue

            order_data = await asyncio.to_thread(self.client.get_orders, bet["order_id"])
            if not order_data:
                still_open.append(bet)
                continue

            markets = order_data.get("markets", [])
            market_info = markets[0] if markets else None
            settled_at = market_info.get("settledAt") if market_info else None

            if not settled_at:
                self.log(f"Waiting on result for '{bet['event_name']}' — not settled yet.")
                still_open.append(bet)
                continue

            trades = order_data.get("trades", [])
            if not trades:
                self.log(f"'{bet['event_name']}' settled with no matched trade — dropping (void/unmatched).")
                continue

            total_profit_loss = sum(t.get("profitLoss", 0) or 0 for t in trades)
            outcome = "won" if total_profit_loss > 0 else "lost"
            result_label = "Won" if outcome == "won" else "Lost"

            if bet.get("record_id"):
                record_bet_settled(bet["record_id"], outcome, bet["price"], bet["stake"])

            if self.strategy_type == "compound":
                if outcome == "won":
                    self.balance = round(self.balance * bet["price"], 2)
                else:
                    self.balance = 0.0

                settle_msg = (
                    f"Settled [{self.name}]\n"
                    f"Match: {bet['event_name']}\n"
                    f"Result: {result_label}\n"
                    f"Balance: {self.balance} (target {self.compound_target})"
                )
                self.log(settle_msg)
                self.client.send_telegram(settle_msg)

                if self.balance <= 0:
                    self.log("💥 Balance hit 0. Disabling.")
                    await disable_strategy(self.name, "balance hit 0")
                elif self.balance >= self.compound_target:
                    self.log(f"🏁 Target {self.compound_target} reached. Disabling.")
                    await disable_strategy(self.name, "target reached")
            else:
                self.current_step = 1 if outcome == "won" else (
                    self.current_step + 1 if self.current_step < self.max_steps else 1
                )

                settle_msg = (
                    f"Settled [{self.name}]\n"
                    f"Match: {bet['event_name']}\n"
                    f"Result: {result_label}\n"
                    f"Next step: {self.current_step}/{self.max_steps}"
                )
                self.log(settle_msg)
                self.client.send_telegram(settle_msg)

        self.active_bets = still_open
        self._save()
