"""Finds a betting opportunity in a 'Full Time Result' / moneyline style
market (BetDEX marketType e.g. FOOTBALL_FULL_TIME_RESULT).

Looks at every outcome (Home / Draw / Away), finds the best "For" (back)
price for each, and returns the first one that falls in the strategy's
odds range with enough liquidity.
"""


def _best_for_price(prices_list, outcome_id):
    """Best back price for one outcome = lowest 'For' price on offer."""
    candidates = [p for p in prices_list if p.get("side") == "For" and p.get("outcomeId") == outcome_id]
    if not candidates:
        return None, None
    best = min(candidates, key=lambda p: p.get("price", float("inf")))
    return best.get("price"), best.get("amount")


def find_opportunity(outcomes, prices_entry, strategy):
    """outcomes = list of resolved outcome dicts (each has "id", "title").
    prices_entry = one item from GET /market-prices "prices" list
    (has "prices": [{side, outcomeId, price, amount}, ...]).

    Returns (outcome_id, outcome_title, price) or None.
    """
    prices_list = prices_entry.get("prices", [])

    for outcome in outcomes:
        outcome_id = outcome.get("id")
        title = outcome.get("title")

        price, amount = _best_for_price(prices_list, outcome_id)
        if price is None:
            continue
        if not (strategy["min_back_odds"] <= price <= strategy["max_back_odds"]):
            continue

        min_liquidity = strategy.get("minimum_liquidity")
        if min_liquidity and amount is not None and amount < min_liquidity:
            continue

        return outcome_id, title, price

    return None
