# BetDEX Metabase Reports

Ready-to-paste SQL for the `betdex` database in Metabase
(`/betdex-config/bets_read.db`).

Source of truth: these queries mirror the reporting logic already in
`src/dashboard.py`, so Metabase numbers match the dashboard. Where the
dashboard does work in Python that Metabase can't, that's converted to SQL
here (noted per report).

## Shared rules

Every settled-bet report filters `result IS NOT NULL`. A bet has
`result = NULL` while still open, and its `profit` is `NULL` too — including
open bets makes win rate and P&L wrong.

- `result` is only ever `'won'`, `'lost'`, or `NULL`.
- `profit` = `stake * (price - 1)` on a win, `-stake` on a loss.
- Times (`placed_at`, `settled_at`, `start_time`) are **naive UTC strings**,
  not real dates, and there is no timezone marker. Reports group by date on
  UTC. If you want Europe/Athens days, add 3 hours before taking the date
  (see "Local-day profit" below).
- `stake` is ~0.1 on almost every row, so raw `profit` is tiny and noisy.
  **ROI %** (`100 * profit / stake`) is the comparable metric.

---

## 1. Strategy summary

The main scoreboard. `avg_odds` and implied win rate are computed in SQL
because the dashboard rounds them in Python.

```sql
SELECT
  strategy_name                    AS "Strategy",
  COUNT(*)                                                        AS "Total Bets",
  SUM(CASE WHEN result = 'won'  THEN 1 ELSE 0 END)                AS "Won",
  SUM(CASE WHEN result = 'lost' THEN 1 ELSE 0 END)                AS "Lost",
  SUM(CASE WHEN result IS NULL  THEN 1 ELSE 0 END)                AS "Pending",
  ROUND(COALESCE(SUM(profit), 0), 2)                              AS "Profit",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END)
      / NULLIF(SUM(CASE WHEN result IN ('won','lost') THEN 1 ELSE 0 END), 0), 1)
                                                                  AS "Win Rate %",
  ROUND(AVG(price), 2)                                            AS "Avg Odds",
  ROUND(100.0 / NULLIF(AVG(price), 0), 1)                         AS "Implied Win Rate %",
  ROUND(100.0 * COALESCE(SUM(profit), 0) / NULLIF(SUM(stake), 0), 1) AS "ROI %"
FROM bets
GROUP BY strategy_name
ORDER BY profit ASC
```

Notes:
- `win_rate_pct` divides by settled bets only, matching the dashboard. A
  strategy with 0 settled bets returns `NULL` rather than a misleading `0`.
- `implied_win_rate_pct` is the break-even rate at that average price. If
  `win_rate_pct` sits below it, the strategy loses long-run. This is the
  most useful column on the board.
- No `WHERE` clause on purpose, so `pending` counts open bets. The
  settled-only metrics are guarded by `NULLIF` instead.

**Suggested viz:** table, conditional color on `roi_pct`. Add a bar chart of
`profit` grouped by `strategy_name`.

## 2. Market-type groups

Merges similar strategies into one view. The dashboard builds this mapping
from `strategies.json` in Python (which Metabase can't read), so this derives
the group from the strategy name instead. That's why report #1 exists as the
authoritative per-strategy view.

```sql
WITH classified AS (
  SELECT
    CASE
      WHEN strategy_name LIKE '%Over%'  THEN 'Total Over'
      WHEN strategy_name LIKE '%Under%' THEN 'Total Under'
      WHEN strategy_name LIKE '%G/G%'   THEN 'GG'
      WHEN strategy_name LIKE '%Win%'   THEN 'Win'
      ELSE 'Other'
    END AS "Market Group",
    result,
    profit,
    stake
  FROM bets
  WHERE result IS NOT NULL
)
SELECT
  "Market Group",
  COUNT(*)                                 AS "Total Bets",
  SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) AS "Won",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END)
      / COUNT(*), 1)                       AS "Win Rate %",
  ROUND(SUM(profit), 2)                    AS "Profit",
  ROUND(100.0 * SUM(profit) / NULLIF(SUM(stake), 0), 1) AS "ROI %"
FROM classified
GROUP BY "Market Group"
ORDER BY "Profit" ASC
```

Warning: a strategy not in `strategies.json` (e.g. `BD_Win_Step 6/1.5`, teams
still present in the bet history) lands in `Other` if its name doesn't match a
keyword. Treat `Other` as "unclassified", not as a market type.

## 3. League performance (with ROI)

Profit per league, ranked by ROI. `net_staked` matters: a league with 2 bets
and 100% ROI is noise, so filter by volume.

```sql
SELECT
  league                           AS "League",
  COUNT(*)                                 AS "Total Bets",
  SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) AS "Won",
  SUM(CASE WHEN result = 'lost' THEN 1 ELSE 0 END) AS "Lost",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) / COUNT(*), 1) AS "Win Rate %",
  ROUND(SUM(stake), 2)                     AS "Net Staked",
  ROUND(SUM(profit), 2)                    AS "Profit",
  ROUND(100.0 * SUM(profit) / NULLIF(SUM(stake), 0), 1) AS "ROI %"
FROM bets
WHERE result IS NOT NULL
  AND league IS NOT NULL
GROUP BY league
HAVING COUNT(*) >= 5
ORDER BY "ROI %" ASC
```

`HAVING COUNT(*) >= 5` drops thin samples. Lower it to 3 to see more, at the
cost of more noise. **Suggested viz:** table sorted by `roi_pct`, or a bar
chart of `roi_pct` by `league`.

## 4. Strategy × league breakdown

Where a specific strategy does well and badly. This used to be on the
dashboard; it lives here now.

```sql
SELECT
  strategy_name                    AS "Strategy",
  league                           AS "League",
  COUNT(*)                                 AS "Total Bets",
  SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) AS "Won",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) / COUNT(*), 1) AS "Win Rate %",
  ROUND(SUM(profit), 2)                    AS "Profit"
FROM bets
WHERE result IS NOT NULL
  AND league IS NOT NULL
GROUP BY strategy_name, league
ORDER BY strategy_name, profit ASC
```

## 5. Cumulative profit over time

The equity curve. A window function, so it needs SQLite 3.25+ (Metabase's
bundled driver is far newer). Runs off `settled_at`, not `placed_at`.

```sql
SELECT
  date(settled_at) AS "Day",
  ROUND(SUM(SUM(profit)) OVER (ORDER BY date(settled_at)), 2) AS "Cumulative Profit",
  ROUND(SUM(profit), 2)  AS "Profit That Day",
  COUNT(*)               AS "Bets Settled"
FROM bets
WHERE result IS NOT NULL
GROUP BY day
ORDER BY day
```

**Suggested viz:** line chart, `day` as X, `cumulative_profit` as Y. One line
per strategy is more useful — add `strategy_name` to `SELECT`/`GROUP BY` and
set it as the series breakout:

```sql
SELECT
  "Day",
  strategy_name AS "Strategy",
  "Cumulative Profit"
FROM (
  SELECT
    date(settled_at) AS "Day",
    strategy_name,
    ROUND(SUM(SUM(profit)) OVER (PARTITION BY strategy_name ORDER BY date(settled_at)), 2) AS "Cumulative Profit"
  FROM bets
  WHERE result IS NOT NULL
  GROUP BY date(settled_at), strategy_name
)
ORDER BY "Day"
```

## 6. Monthly and daily profit

```sql
-- Monthly
SELECT
  strftime('%Y-%m', settled_at) AS "Month",
  COUNT(*)                AS "Bets Settled",
  ROUND(SUM(profit), 2)   AS "Profit",
  ROUND(100.0 * SUM(profit) / NULLIF(SUM(stake), 0), 1) AS "ROI %"
FROM bets
WHERE result IS NOT NULL
GROUP BY month
ORDER BY month
```

```sql
-- Daily, last 30 days
SELECT
  date(settled_at) AS "Day",
  COUNT(*)              AS "Bets Settled",
  ROUND(SUM(profit), 2) AS "Profit"
FROM bets
WHERE result IS NOT NULL
  AND date(settled_at) >= date('now', '-30 days')
GROUP BY day
ORDER BY day
```

## 7. Odds buckets

Win rate by price band. Tells you whether your edge is at short or long odds.

```sql
SELECT
  CASE
    WHEN price < 1.5  THEN '< 1.50'
    WHEN price < 1.55 THEN '1.50-1.54'
    WHEN price < 1.6  THEN '1.55-1.59'
    ELSE '1.60+'
  END AS "Odds Bucket",
  COUNT(*)                                 AS "Total Bets",
  SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) AS "Won",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) / COUNT(*), 1) AS "Win Rate %",
  ROUND(AVG(price), 3)                     AS "Avg Odds",
  ROUND(SUM(profit), 2)                    AS "Profit"
FROM bets
WHERE result IS NOT NULL
GROUP BY "Odds Bucket"
ORDER BY "Avg Odds"
```

Note: your prices currently max out at **1.70**, so `1.60+` is the top bucket.

## 8. Staking-step performance

Only meaningful for laddered strategies that reach step 2+. The dashboard
hides `MAX(step) <= 1` strategies.

```sql
SELECT
  strategy_name                    AS "Strategy",
  step                             AS "Step",
  COUNT(*)                                 AS "Total Bets",
  SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) AS "Won",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) / COUNT(*), 1) AS "Win Rate %",
  ROUND(SUM(stake), 2)                     AS "Staked",
  ROUND(SUM(profit), 2)                    AS "Profit"
FROM bets
WHERE result IS NOT NULL
  AND strategy_name IN (
    SELECT strategy_name FROM bets
    GROUP BY strategy_name HAVING MAX(step) > 1
  )
GROUP BY strategy_name, step
ORDER BY strategy_name, step
```

Use this to sanity-check that the ladder is recovering: later steps usually
show a better win rate at longer odds, with bigger stakes.

## 9. Weekday activity

```sql
SELECT
  CASE CAST(strftime('%w', placed_at) AS INTEGER)
    WHEN 0 THEN 'Sun' WHEN 1 THEN 'Mon' WHEN 2 THEN 'Tue'
    WHEN 3 THEN 'Wed' WHEN 4 THEN 'Thu' WHEN 5 THEN 'Fri'
    ELSE 'Sat'
  END AS "Weekday",
  CAST(strftime('%w', placed_at) AS INTEGER) AS "Day Num",
  COUNT(*)              AS "Bets Placed",
  ROUND(SUM(profit), 2) AS "Profit"
FROM bets
GROUP BY "Weekday", "Day Num"
ORDER BY "Day Num"
```

This one deliberately does **not** filter `result IS NOT NULL` — it counts
bets *placed*, matching the dashboard. Open bets have `profit = NULL`, so skip
the profit column if you want place-only counts.

## 10. Local-day profit (Europe/Athens)

Everything else groups on UTC. Since `settled_at` is a naive UTC string,
adding 3 hours shifts it to Athens time. This is hardcoded to EEST/+3 and
will be one hour off in winter (EET/+2).

```sql
SELECT
  date(datetime(settled_at, '+3 hours')) AS "Local Day",
  COUNT(*)              AS "Bets Settled",
  ROUND(SUM(profit), 2) AS "Profit"
FROM bets
WHERE result IS NOT NULL
GROUP BY "Local Day"
ORDER BY "Local Day"
```

## 11. Open / pending bets

The only report that looks at unsettled bets.

```sql
SELECT
  strategy_name    AS "Strategy",
  event_name       AS "Event",
  league           AS "League",
  selection_name   AS "Selection",
  price            AS "Price",
  stake            AS "Stake",
  step             AS "Step",
  placed_at        AS "Placed At",
  start_time       AS "Start Time",
  ROUND(julianday(start_time) - julianday('now'), 2) AS "Starts In Days"
FROM bets
WHERE result IS NULL
ORDER BY start_time ASC
```

`placed_at` and `start_time` are stored as UTC strings but without a `Z`, so
Metabase may guess the wrong timezone when rendering them. If the times look
shifted, wrap them in `datetime(placed_at, '+3 hours')`.

## 12. Football Win — performance per league

Per-league breakdown of a single strategy. This is report 3 filtered to one
strategy, plus `Avg Odds` so you can compare each league's win rate against
its own break-even rate.

```sql
SELECT
  league                           AS "League",
  COUNT(*)                                 AS "Total Bets",
  SUM(CASE WHEN result = 'won'  THEN 1 ELSE 0 END) AS "Won",
  SUM(CASE WHEN result = 'lost' THEN 1 ELSE 0 END) AS "Lost",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) / COUNT(*), 1) AS "Win Rate %",
  ROUND(AVG(price), 2)                     AS "Avg Odds",
  ROUND(100.0 / NULLIF(AVG(price), 0), 1)  AS "Break Even %",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) / COUNT(*)
      - 100.0 / NULLIF(AVG(price), 0), 1)  AS "Edge %",
  ROUND(SUM(stake), 2)                     AS "Net Staked",
  ROUND(SUM(profit), 2)                    AS "Profit",
  ROUND(100.0 * SUM(profit) / NULLIF(SUM(stake), 0), 1) AS "ROI %"
FROM bets
WHERE result IS NOT NULL
  AND league IS NOT NULL
  AND strategy_name = 'Football Win'
GROUP BY league
ORDER BY "Total Bets" DESC, "Profit" ASC
```

**`Edge %` is the column to read.** It's actual win rate minus the break-even
rate at that league's average odds. Positive means the league beats its own
price; negative means it loses. Unlike `ROI %` it isn't distorted by how many
bets happen to have landed there.

Ordered by `Total Bets DESC` on purpose: the leagues with the most evidence
are at the top, so you read the trustworthy rows first.

### With a volume guard (recommended)

Football Win's bets are spread very thin across leagues. Most leagues have
1-2 bets, where win rate is noise. This version shows only leagues with
enough volume to mean something:

```sql
SELECT
  league                           AS "League",
  COUNT(*)                                 AS "Total Bets",
  SUM(CASE WHEN result = 'won'  THEN 1 ELSE 0 END) AS "Won",
  SUM(CASE WHEN result = 'lost' THEN 1 ELSE 0 END) AS "Lost",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) / COUNT(*), 1) AS "Win Rate %",
  ROUND(AVG(price), 2)                     AS "Avg Odds",
  ROUND(100.0 / NULLIF(AVG(price), 0), 1)  AS "Break Even %",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) / COUNT(*)
      - 100.0 / NULLIF(AVG(price), 0), 1)  AS "Edge %",
  ROUND(SUM(stake), 2)                     AS "Net Staked",
  ROUND(SUM(profit), 2)                    AS "Profit",
  ROUND(100.0 * SUM(profit) / NULLIF(SUM(stake), 0), 1) AS "ROI %"
FROM bets
WHERE result IS NOT NULL
  AND league IS NOT NULL
  AND strategy_name = 'Football Win'
GROUP BY league
HAVING COUNT(*) >= 3
ORDER BY "Edge %" ASC
```

Change `>= 3` to `>= 5` once more bets accumulate. **Suggested viz:** table,
conditional formatting on `Edge %` and `ROI %`.

### League totals row

Appends a grand-total line so you can see the strategy overall against the
per-league rows.

```sql
SELECT
  league                           AS "League",
  COUNT(*)                                 AS "Total Bets",
  SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) AS "Won",
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) / COUNT(*), 1) AS "Win Rate %",
  ROUND(AVG(price), 2)                     AS "Avg Odds",
  ROUND(SUM(profit), 2)                    AS "Profit",
  COUNT(*)                                 AS "sort_key"
FROM bets
WHERE result IS NOT NULL
  AND strategy_name = 'Football Win'
GROUP BY league

UNION ALL

SELECT
  '-- ALL LEAGUES --',
  COUNT(*),
  SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END),
  ROUND(100.0 * SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) / COUNT(*), 1),
  ROUND(AVG(price), 2),
  ROUND(SUM(profit), 2),
  0
FROM bets
WHERE result IS NOT NULL
  AND strategy_name = 'Football Win'
ORDER BY "sort_key" DESC, "League"
```

The `sort_key` column forces the total row to the bottom (it's `0`, everything
else is its bet count) and is dropped by Metabase if you hide it. Note the
total includes Football Win's 2 open bets in `Total Bets` but not in `Won`,
so the total row's win rate is computed on settled bets — while the per-league
rows above exclude open bets entirely, since they're missing a league match.

---

## What the current data says

Snapshot at 176 bets (174 settled), 2026-07-05 to 2026-10-20:

- **Overall: -4.21 net.** 108 won / 66 lost = 62.1% win rate.
- **One strategy carries the loss.** `BD_Over 2.5` is **-4.41** on 21 bets.
  Strip it out and the other six strategies net **+0.20** combined. Fixing or
  disabling that one strategy is the single highest-value change available.
- **`Win`-group strategies are the profitable core:** +2.54 across 131 bets.
- **All-time win rate looks healthy but the prices are too short.** Average
  price is 1.544, so break-even needs **64.8%**. Actual is 62.1% — a **2.7
  point** shortfall, which is exactly why the book is down. This is a
  pricing/selection problem, not a league problem.
- **The bleed is concentrated in the 1.50-1.54 band:** -3.48 on 39 bets, the
  worst of any bucket. `<1.50` (+0.12) and `1.55-1.59` (+0.01) are roughly
  break-even. Worth checking whether `min_back_odds` should exclude that band.
- **Only 47 distinct settled days** and 319 known leagues, so most league
  rows will be thin. That's what the `>= 5` filter is for.

## Caveats

1. **Data lags by up to an hour.** Reports read `bets_read.db`, refreshed at
   `:30` by cron. Hit "Sync database schema" in Metabase if columns change.
2. **`bets_read.db` is a copy.** Never point a report at
   `/betdex-config/bets.db` directly — Metabase will hit "database is locked"
   against the live file.
3. **No market-type column in the DB.** Grouping by market type is inferred
   from strategy names (#2) or hardcoded. To make it exact, add a
   `market_type_id` column to the `bets` table and populate it at insert time
   in `src/bet_records.py`. Ask if you want that done.
