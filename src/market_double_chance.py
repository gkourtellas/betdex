"""Checks the Match Odds market for a trigger (Home or Away back odds in
range), then places the bet on the matching Double Chance runner from
the SAME event — not the Match Odds market itself.

This is a normal back bet (unlike market_lay_opponent.py), so cash-out
works on it without any special math.

Confirmed against real Matchbook data (2026-06-27):
  Double Chance runners come in this order:
    0: "<Home> or Draw"
    1: "<Home> or <Away>"
    2: "Draw or <Away>"
  Match Odds runners: 0 = Home, 1 = Away, 2 = Draw (confirmed earlier).
"""


def _passes_spread(prices, back_odds, strategy):
    max_spread = strategy.get("max_spread_percent")
    if not max_spread:
        return True
    lays = [p for p in prices if p.get("side") == "lay"]
    if not lays:
        return True
    best_lay = min(lays, key=lambda p: p.get("odds", float("inf")))
    lay_odds = best_lay.get("odds")
    if not lay_odds:
        return True
    spread_pct = ((lay_odds - back_odds) / back_odds) * 100
    return spread_pct <= max_spread


def find_opportunity_in_event(event, strategy):
    """Returns (runner_id, runner_name, odds) for the Double Chance
    runner to back, or None if no trigger / no Double Chance market
    is available for this event.
    """
    match_odds_market = None
    double_chance_market = None
    for market in event.get("markets", []):
        name = market.get("name")
        if name == "Match Odds":
            match_odds_market = market
        elif name == "Double Chance":
            double_chance_market = market

    if not match_odds_market or not double_chance_market:
        return None

    mo_runners = match_odds_market.get("runners", [])
    if len(mo_runners) < 2:
        return None
    home, away = mo_runners[0], mo_runners[1]

    home_back = _best_back_odds(home)
    away_back = _best_back_odds(away)

    trigger_side = None
    if home_back is not None and strategy["min_back_odds"] <= home_back <= strategy["max_back_odds"]:
        trigger_side = "home"
    elif away_back is not None and strategy["min_back_odds"] <= away_back <= strategy["max_back_odds"]:
        trigger_side = "away"

    if trigger_side is None:
        return None

    dc_runners = double_chance_market.get("runners", [])
    if len(dc_runners) < 3:
        return None

    # "<Home> or Draw" covers a Home win or a draw (runner 0).
    # "Draw or <Away>" covers an Away win or a draw (runner 2).
    target_runner = dc_runners[0] if trigger_side == "home" else dc_runners[2]

    prices = target_runner.get("prices", [])
    backs = [p for p in prices if p.get("side") == "back"]
    if not backs:
        return None

    best = min(backs, key=lambda p: p.get("odds", float("inf")))
    odds = best.get("odds")
    size_available = best.get("available-amount", best.get("available_amount"))

    if odds is None:
        return None

    min_liquidity = strategy.get("minimum_liquidity")
    if min_liquidity and size_available is not None and size_available < min_liquidity:
        return None

    if not _passes_spread(prices, odds, strategy):
        return None

    return target_runner.get("id"), target_runner.get("name"), odds, double_chance_market.get("id")


def _best_back_odds(runner):
    backs = [p for p in runner.get("prices", []) if p.get("side") == "back"]
    if not backs:
        return None
    best = min(backs, key=lambda p: p.get("odds", float("inf")))
    return best.get("odds")
