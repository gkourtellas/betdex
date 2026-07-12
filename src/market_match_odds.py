"""Finds a betting opportunity in a 'Full Time Result' / moneyline style
market (BetDEX marketType e.g. FOOTBALL_FULL_TIME_RESULT).

Confirmed against real site data: site "Back" column = API side
"Against". Site "Lay" column = API side "For". Backing an outcome
means reading the "Against" price list, not "For".

Spread and two-sided liquidity are handled by market_quality.py — see
that module for the full explanation of back/lay price semantics.
"""

from market_quality import best_back, passes_quality_checks


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

        price, _amount = best_back(prices_list, outcome_id)
        if price is None:
            continue
        if not (strategy["min_back_odds"] <= price <= strategy["max_back_odds"]):
            continue

        ok, _price, _amount, _reason = passes_quality_checks(
            prices_list,
            outcome_id,
            max_spread_pct=strategy.get("max_spread_pct"),
            minimum_liquidity=strategy.get("minimum_liquidity"),
        )
        if not ok:
            continue

        return outcome_id, title, price

    return None
