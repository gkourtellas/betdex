"""Finds a betting opportunity in an Over/Under Total Goals market
(BetDEX marketType e.g. FOOTBALL_OVER_UNDER_TOTAL_GOALS).

Confirmed against real site data: site "Back" column = API side
"Against". Backing an outcome means reading the "Against" price list.

On BetDEX each goal line is its OWN market (marketValue = "2.5", "3.5",
etc), with exactly two outcomes: "Over" and "Under". So this only needs
to check the one outcome matching the strategy's chosen direction.

NOTE: outcome titles are actually "Over 2.5" / "Under 2.5" (line
included in the title), not bare "Over"/"Under" — so direction is
matched on the first word, not the whole title.

Spread and two-sided liquidity are handled by market_quality.py — see
that module for the full explanation of back/lay price semantics.
"""

from market_quality import best_back, passes_quality_checks


def market_matches_line(market, strategy):
    """True if this market's line fits the strategy.

    Supports two config styles:
    - exact line: strategy["total_range"] = "2.5"
    - a range: strategy["total_range_min"] and strategy["total_range_max"]
      (e.g. 8.5 to 10.5) — any line in between counts as a match.
    """
    value = market.get("marketValue")
    if value is None:
        return False
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False

    line_min = strategy.get("total_range_min")
    line_max = strategy.get("total_range_max")
    if line_min is not None and line_max is not None:
        return float(line_min) <= value <= float(line_max)

    wanted_line = str(strategy.get("total_range", "")).strip()
    return str(value) == wanted_line or value == float(wanted_line or "nan")


def find_opportunity(outcomes, prices_entry, strategy):
    """outcomes = list of resolved outcome dicts (each has "id", "title").
    Same prices_entry shape as market_match_odds.find_opportunity.
    Only backs the outcome titled strategy['total_direction']
    ("Over" or "Under") — titles look like "Over 2.5", so we match on
    the first word only.
    """
    wanted_direction = str(strategy.get("total_direction", "")).strip().lower()
    prices_list = prices_entry.get("prices", [])

    for outcome in outcomes:
        title = (outcome.get("title") or "")
        title_words = title.strip().lower().split()
        if not title_words or title_words[0] != wanted_direction:
            continue

        outcome_id = outcome.get("id")

        price, _amount = best_back(prices_list, outcome_id)
        if price is None:
            return None
        if not (strategy["min_back_odds"] <= price <= strategy["max_back_odds"]):
            return None

        ok, _price, _amount, _reason = passes_quality_checks(
            prices_list,
            outcome_id,
            max_spread_pct=strategy.get("max_spread_pct"),
            minimum_liquidity=strategy.get("minimum_liquidity"),
        )
        if not ok:
            return None

        return outcome_id, title, price

    return None
