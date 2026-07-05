"""Finds a betting opportunity in a 'Full Time Result' / moneyline style
market (BetDEX marketType e.g. FOOTBALL_FULL_TIME_RESULT).

Confirmed against real site data: site "Back" column = API side
"Against". Site "Lay" column = API side "For". Backing an outcome
means reading the "Against" price list, not "For".
"""


def _best_for_price(prices_list, outcome_id):
    """Best price to back at = highest 'Against' price on offer.
    Order itself is still placed with side='For'."""
    candidates = [p for p in prices_list if p.get("side") == "Against" and p.get("outcomeId") == outcome_id]
    if not candidates:
        return None, None
    best = max(candidates, key=lambda p: p.get("price", 0))
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
