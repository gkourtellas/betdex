"""Finds a betting opportunity in an Over/Under Total Goals market
(BetDEX marketType e.g. FOOTBALL_OVER_UNDER_TOTAL_GOALS).

Confirmed against real site data: site "Back" column = API side
"Against". Backing an outcome means reading the "Against" price list.

On BetDEX each goal line is its OWN market (marketValue = "2.5", "3.5",
etc), with exactly two outcomes: "Over" and "Under". So this only needs
to check the one outcome matching the strategy's chosen direction.
"""


def market_matches_line(market, strategy):
    """True if this market's marketValue equals strategy['total_range']."""
    wanted_line = str(strategy.get("total_range", "")).strip()
    return str(market.get("marketValue", "")).strip() == wanted_line


def find_opportunity(outcomes, prices_entry, strategy):
    """outcomes = list of resolved outcome dicts (each has "id", "title").
    Same prices_entry shape as market_match_odds.find_opportunity.
    Only backs the outcome titled strategy['total_direction']
    ("Over" or "Under").
    """
    wanted_direction = str(strategy.get("total_direction", "")).strip().lower()
    prices_list = prices_entry.get("prices", [])

    for outcome in outcomes:
        title = (outcome.get("title") or "")
        if title.strip().lower() != wanted_direction:
            continue

        outcome_id = outcome.get("id")
        candidates = [p for p in prices_list if p.get("side") == "Against" and p.get("outcomeId") == outcome_id]
        if not candidates:
            return None
        best = max(candidates, key=lambda p: p.get("price", 0))
        price = best.get("price")
        amount = best.get("amount")

        if price is None:
            return None
        if not (strategy["min_back_odds"] <= price <= strategy["max_back_odds"]):
            return None

        min_liquidity = strategy.get("minimum_liquidity")
        if min_liquidity and amount is not None and amount < min_liquidity:
            return None

        return outcome_id, title, price

    return None
