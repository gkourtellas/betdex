"""Shared "market quality" checks: back/lay spread and two-sided liquidity.

BetDEX price list semantics (confirmed against real site data, see also
the notes in market_match_odds.py):

- Entries with side="Against" are resting LAY offers from other users.
  Reading these tells you the best price YOU could back at right now
  (you'd submit your own order with side="For" to match one of them).
  Best back price = the HIGHEST price among "Against" entries for that
  outcome (higher odds = better for the backer).

- Entries with side="For" are resting BACK offers from other users.
  Reading these tells you the best price YOU could lay at right now
  (you'd submit your own order with side="Against" to match one of
  them). Best lay price = the LOWEST price among "For" entries for
  that outcome (lower odds = better for the layer).

In a healthy, liquid market the best lay price sits at or above the
best back price — there's a small gap between what you could back at
and what you could lay at. The wider that gap (as a % of the back
price), the thinner or more stale the market probably is. This module
gives strategies a shared, reusable way to skip those markets, and to
require real depth on BOTH sides — not just the side we're about to
bet into, but also the side we'd need if we ever want to cash out
(lay off) an open position.
"""


def _best(prices_list, outcome_id, side, pick):
    candidates = [
        p for p in prices_list
        if p.get("side") == side and p.get("outcomeId") == outcome_id
    ]
    if not candidates:
        return None, None
    best = pick(candidates, key=lambda p: p.get("price", 0))
    return best.get("price"), best.get("amount")


def best_back(prices_list, outcome_id):
    """Best price/amount to BACK at right now (highest 'Against' offer)."""
    return _best(prices_list, outcome_id, "Against", max)


def best_lay(prices_list, outcome_id):
    """Best price/amount to LAY at right now (lowest 'For' offer)."""
    return _best(prices_list, outcome_id, "For", min)


def spread_pct(back_price, lay_price):
    """Gap between lay and back price, as a % of the back price.

    Returns None if either price is missing (e.g. no lay-side offers
    at all) or back_price is 0, since the % can't be computed.
    """
    if back_price is None or lay_price is None or back_price == 0:
        return None
    return (lay_price - back_price) / back_price * 100


def passes_quality_checks(prices_list, outcome_id, max_spread_pct=None, minimum_liquidity=None):
    """Run the configured quality checks for one outcome.

    max_spread_pct: reject if (lay - back) / back * 100 exceeds this.
        None/0 disables the check.
    minimum_liquidity: reject if the amount available on EITHER side
        (back or lay) is below this. None/0 disables the check.

    Returns (ok, back_price, back_amount, reason). back_price/back_amount
    are returned even on failure so callers can log/print context.
    reason is a short human-readable string, or None when ok=True.
    """
    back_price, back_amount = best_back(prices_list, outcome_id)
    if back_price is None:
        return False, None, None, "no back price available"

    lay_price, lay_amount = best_lay(prices_list, outcome_id)

    if minimum_liquidity:
        if back_amount is None or back_amount < minimum_liquidity:
            return False, back_price, back_amount, "back-side liquidity below minimum"
        if lay_amount is None or lay_amount < minimum_liquidity:
            return False, back_price, back_amount, "lay-side liquidity below minimum (needed to cash out)"

    if max_spread_pct:
        gap = spread_pct(back_price, lay_price)
        if gap is None:
            return False, back_price, back_amount, "no lay price available to measure spread"
        if gap > max_spread_pct:
            return False, back_price, back_amount, f"spread {gap:.1f}% wider than max {max_spread_pct}%"

    return True, back_price, back_amount, None
