"""Automated BetDEX strategy loop (scan, bet, settle, step ladder).

Mirrors matchbook src/strategy_one.py logic and structure.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from api_client import BetdexClient
from log_util import install_print_logger, setup_logging

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "state.json")


def load_settings():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing config: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def stake_for_step(settings, step):
    mode = settings.get("mode", "production")
    ladder = settings.get("stakes", {}).get(mode)
    if not ladder:
        ladder = settings.get("stakes", {}).get("production", [0.10])
    idx = max(0, min(step - 1, len(ladder) - 1))
    return float(ladder[idx])


def load_state():
    if not os.path.isfile(STATE_FILE):
        return 1, None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            current_step = state.get("current_step", 1)
            active_bet_info = state.get("active_bet_info")
            if active_bet_info:
                active_bet_info["start_time"] = datetime.fromisoformat(active_bet_info["start_time"])
                active_bet_info["placed_at"] = datetime.fromisoformat(active_bet_info["placed_at"])
            return current_step, active_bet_info
    except Exception as e:
        print(f"⚠️ Error loading state file: {str(e)}. Falling back to clean slate.")
        return 1, None


def save_state(current_step, active_bet_info):
    try:
        state_to_save = {"current_step": current_step, "active_bet_info": None}
        if active_bet_info:
            state_to_save["active_bet_info"] = {
                **active_bet_info,
                "start_time": active_bet_info["start_time"].isoformat(),
                "placed_at": active_bet_info["placed_at"].isoformat(),
            }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_to_save, f, indent=2)
    except Exception as e:
        print(f"⚠️ Error updating state file: {str(e)}")


def main():
    log_path = setup_logging()
    install_print_logger()
    print(f"Log file: {log_path}")
    print("Starting automated execution strategy loop...")
    client = BetdexClient()

    if not client.login():
        print("Initial authentication failed.")
        return

    settings = load_settings()
    max_steps = int(settings.get("max_steps", 6))
    price_min = float(settings.get("odds_min", 1.45))
    price_max = float(settings.get("odds_max", 1.60))
    mode = settings.get("mode", "production")
    market_type_id = settings.get("market_type_id", "FOOTBALL_FULL_TIME_RESULT")
    print(
        f"Config loaded: mode={mode}, max_steps={max_steps}, "
        f"price {price_min}-{price_max}, stake step 1={stake_for_step(settings, 1)}"
    )

    loop_interval = 15

    current_step, active_bet_info = load_state()
    if active_bet_info:
        print(f"🔄 Recovered existing active bet profile for: {active_bet_info['event_name']}")

    try:
        while True:
            if not active_bet_info:
                print(f"\n--- Scanning markets at {time.strftime('%Y-%m-%d %H:%M:%S')} (Step {current_step}/{max_steps}) ---")

                now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                data = client.get_markets(
                    market_type_ids=market_type_id,
                    from_datetime=now_iso,
                    statuses="Open",
                    published=True,
                    size=100,
                    sort="lockAt,asc",
                )

                if data and "markets" in data:
                    for market in data["markets"]:
                        if market.get("inPlayStatus") == "InPlay":
                            continue
                        if market.get("suspended"):
                            continue

                        lock_at_str = market.get("lockAt")
                        if not lock_at_str:
                            continue
                        try:
                            lock_at = datetime.strptime(lock_at_str, "%Y-%m-%dT%H:%M:%S.%fZ")
                        except Exception:
                            continue
                        if datetime.utcnow() >= lock_at:
                            continue

                        market_id = market.get("id")
                        prices_data = client.get_market_prices(market_id)
                        if not prices_data or not prices_data.get("prices"):
                            continue

                        price_entry = prices_data["prices"][0]
                        for p in price_entry.get("prices", []):
                            if p.get("side") != "For":
                                continue
                            best_price = p.get("price")
                            if best_price is None or not (price_min <= best_price <= price_max):
                                continue

                            outcome_id = p.get("outcomeId")

                            detail = client.get_market_by_id(market_id)
                            if not detail:
                                continue

                            outcome_title = "Unknown"
                            for o in detail.get("marketOutcomes", []):
                                if o.get("id") == outcome_id:
                                    outcome_title = o.get("title")
                                    break

                            event_name = "Unknown Match"
                            league_name = "Unknown League"
                            for ev in detail.get("events", []):
                                event_name = ev.get("name", event_name)
                            for eg in detail.get("eventGroups", []):
                                league_name = eg.get("name", league_name)

                            print(f"🎯 Trigger conditions met for: {event_name} -> {outcome_title}")

                            target_stake = stake_for_step(settings, current_step)

                            order_result = client.submit_order(
                                market_id=market_id,
                                outcome_id=outcome_id,
                                side="For",
                                price=best_price,
                                stake=target_stake,
                            )

                            if order_result:
                                orders = order_result.get("orders", [])
                                placed_order = orders[0] if orders else {}
                                order_id = placed_order.get("id")

                                athens_time = lock_at + timedelta(hours=3)
                                athens_time_str = athens_time.strftime("%H:%M")

                                msg = (
                                    f"🚀 Bet Placed!\n"
                                    f"Step: {current_step}/{max_steps}\n"
                                    f"League: {league_name}\n"
                                    f"Match: {event_name}\n"
                                    f"Selection: {outcome_title}\n"
                                    f"Action: For\n"
                                    f"Price: {best_price}\n"
                                    f"Stake: {target_stake}\n"
                                    f"Start Time: {athens_time_str}"
                                )
                                print(msg)
                                client.send_telegram(msg)

                                active_bet_info = {
                                    "order_id": order_id,
                                    "market_id": market_id,
                                    "start_time": lock_at,
                                    "placed_at": datetime.utcnow(),
                                    "selection_name": outcome_title,
                                    "event_name": event_name,
                                }
                                save_state(current_step, active_bet_info)
                                break
                            else:
                                print("⚠️ Execution routing declined by backend exchange rules.")
                        if active_bet_info:
                            break

            if active_bet_info:
                resume_time = active_bet_info["start_time"] + timedelta(minutes=110)
                resume_athens = resume_time + timedelta(hours=3)

                if datetime.utcnow() < resume_time:
                    print(f"⏳ Active bet track verified. Holding market checks until 110 minutes after kickoff ({resume_athens.strftime('%H:%M')} Athens time)...")

                while datetime.utcnow() < resume_time:
                    time.sleep(30)

                print("⏰ 110 minutes elapsed since kickoff. Transitioning to minute-by-minute status tracking...")

                while True:
                    time.sleep(60)
                    order_id = active_bet_info.get("order_id")
                    if not order_id:
                        print("No order_id — waiting for BetDEX...")
                        continue

                    order_data = client.get_orders(order_ids=order_id)
                    orders = (order_data or {}).get("orders", [])
                    order = orders[0] if orders else None
                    markets = (order_data or {}).get("markets", [])
                    market_info = markets[0] if markets else None

                    if not order:
                        print(f"Checking settlement for order {order_id} (not_found)...")
                        continue

                    settled_at = market_info.get("settledAt") if market_info else None
                    print(f"Checking settlement for order {order_id} (settledAt={settled_at})...")

                    if not settled_at:
                        continue

                    trades = (order_data or {}).get("trades", [])
                    total_profit_loss = sum(t.get("profitLoss", 0) or 0 for t in trades)

                    if not trades:
                        # Market settled but no trade recorded for this order — treat as unmatched/void, skip
                        continue

                    outcome = "won" if total_profit_loss > 0 else "lost"
                    result_label = "Won" if outcome == "won" else "Lost"
                    next_step = 1 if outcome == "won" else (
                        current_step + 1 if current_step < max_steps else 1
                    )

                    settle_msg = (
                        f"Settled\n"
                        f"Match: {active_bet_info['event_name']}\n"
                        f"Result: {result_label}\n"
                        f"Next step: {next_step}/{max_steps}"
                    )
                    print(settle_msg)
                    client.send_telegram(settle_msg)

                    current_step = next_step
                    active_bet_info = None
                    save_state(current_step, active_bet_info)
                    break

            time.sleep(loop_interval)

    except KeyboardInterrupt:
        print("\nStrategy engine loop safely terminated.")


if __name__ == "__main__":
    main()
