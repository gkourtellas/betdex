"""Tests the day-of-week performance report (/api/weekday_performance).

No bot, no network — just the dashboard against a read-only copy of the
bets database. Proves the Mon..Sun aggregation reconciles with raw SQL:
totals, the won/lost split, the strategy filter and the Athens-local day
boundaries.

Run (inside the container, where the deps live):
    docker exec -w /repo betdex_dashboard python test_weekday_performance.py
"""

import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, "src")

import dashboard  # noqa: E402


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "bets.db")


def raw(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get(client, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    resp = client.get("/api/weekday_performance" + ("?" + q if q else ""))
    assert resp.status_code == 200, f"HTTP {resp.status_code}"
    return resp.get_json()


def check(title, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {title}{(' — ' + detail) if detail else ''}")
    assert cond, f"{title} {detail}"


def test_totals_reconcile(client):
    print("\n[1] weekday totals reconcile with raw SQL")
    data = get(client)
    s = data["summary"]

    truth = raw("""
        SELECT COUNT(*) AS bets,
               ROUND(SUM(profit), 4) AS profit,
               ROUND(SUM(stake), 4) AS staked
        FROM bets WHERE result IS NOT NULL
    """)[0]

    check("bet count", s["bets"] == truth["bets"], f"{s['bets']} vs {truth['bets']}")
    check("profit", abs(s["profit"] - truth["profit"]) < 0.01, f"{s['profit']} vs {truth['profit']}")
    check("staked", abs(s["staked"] - truth["staked"]) < 0.05, f"{s['staked']} vs {truth['staked']}")

    summed = round(sum(w["profit"] for w in data["weekdays"]), 4)
    check("weekday rows sum to total profit", abs(summed - s["profit"]) < 0.01, f"{summed} vs {s['profit']}")
    check("weekday rows account for every bet",
          sum(w["bets"] for w in data["weekdays"]) == s["bets"])


def test_shape(client):
    print("\n[2] weekday report shape")
    data = get(client)
    days = data["weekdays"]

    check("seven buckets", len(days) == 7, str(len(days)))
    check("Mon..Sun order",
          [d["name"] for d in days] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
          str([d["name"] for d in days]))
    check("weekday index starts at Mon", days[0]["weekday"] == 0 and days[-1]["weekday"] == 6)

    # each weekday's trading days must equal the number of distinct local dates
    # that fell on it
    expected_days = {}
    for r in raw("SELECT settled_at FROM bets WHERE result IS NOT NULL"):
        day = dashboard._local_day(r["settled_at"])
        if not day:
            continue
        wd = datetime.fromisoformat(day).weekday()
        expected_days.setdefault(wd, set()).add(day)
    for d in days:
        exp = len(expected_days.get(d["weekday"], set()))
        check(f"{d['name']} trading days", d["days"] == exp, f"{d['days']} vs {exp}")

    split = raw("""
        SELECT result, COUNT(*) AS n FROM bets
        WHERE result IS NOT NULL GROUP BY result
    """)
    won = sum(r["n"] for r in split if r["result"] == "won")
    lost = sum(r["n"] for r in split if r["result"] == "lost")
    check("won", sum(d["won"] for d in days) == won)
    check("lost", sum(d["lost"] for d in days) == lost)


def test_avg(client):
    print("\n[3] avg-per-day is consistent with the totals")
    data = get(client)
    for d in data["weekdays"]:
        if d["days"] == 0:
            continue
        expected = round(d["profit"] / d["days"], 4)
        check(f"{d['name']} avg/day", abs(d["avg_profit"] - expected) < 0.001, f"{d['avg_profit']} vs {expected}")

    active = [d for d in data["weekdays"] if d["days"] > 0]
    if active:
        best = max(active, key=lambda d: d["avg_profit"])
        worst = min(active, key=lambda d: d["avg_profit"])
        check("best weekday reported", data["summary"]["best_day"]["name"] == best["name"],
              f"{data['summary']['best_day']['name']} vs {best['name']}")
        check("worst weekday reported", data["summary"]["worst_day"]["name"] == worst["name"],
              f"{data['summary']['worst_day']['name']} vs {worst['name']}")
        check("busiest weekday reported", data["summary"]["busiest"]["name"] ==
              max(active, key=lambda d: d["bets"])["name"])


def test_strategy_filter(client):
    print("\n[4] strategy filter narrows the report correctly")
    names = [r["strategy_name"] for r in raw(
        "SELECT DISTINCT strategy_name FROM bets WHERE strategy_name IS NOT NULL ORDER BY strategy_name"
    )]
    assert names, "no strategies in the database"

    name = names[0]
    data = get(client, strategy=name.replace(" ", "%20"))
    s = data["summary"]
    truth = raw("""
        SELECT COUNT(*) AS bets, ROUND(SUM(profit), 4) AS profit
        FROM bets WHERE result IS NOT NULL AND strategy_name = ?
    """, (name,))[0]

    check("filter echoed back", data["strategy"] == name, data["strategy"])
    check("bet count", s["bets"] == truth["bets"], f"{s['bets']} vs {truth['bets']}")
    check("profit", abs(s["profit"] - truth["profit"]) < 0.01, f"{s['profit']} vs {truth['profit']}")
    check("filtered rows account for every bet",
          sum(w["bets"] for w in data["weekdays"]) == s["bets"])


def test_bad_input(client):
    print("\n[5] bad input does not blow up")
    for qs in ("strategy=does_not_exist", "strategy=", "days=abc"):
        resp = client.get("/api/weekday_performance?" + qs)
        check(f"'{qs}' -> 200", resp.status_code == 200, str(resp.status_code))
        body = resp.get_json()
        check(f"'{qs}' -> usable payload", isinstance(body.get("weekdays"), list))


def main():
    print("=" * 66)
    print("Day-of-week performance report — betdex tests")
    print("=" * 66)
    client = dashboard.app.test_client()

    test_totals_reconcile(client)
    test_shape(client)
    test_avg(client)
    test_strategy_filter(client)
    test_bad_input(client)

    print("\n" + "=" * 66)
    print("ALL TESTS PASSED")
    print("=" * 66)


if __name__ == "__main__":
    main()
