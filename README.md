# betdex

BetDEX trading bot (see `docker-compose.yml`).

## Dashboard

A read-only web dashboard runs on **http://localhost:8051** (service `dashboard`).
It reads `config/bets.db` — it never places bets. Set `DASHBOARD_PASSWORD` in
`.env` to require a login when exposed through a tunnel.

The dashboard was ported from the Matchbook bot so it looks and feels the same.
Pages:

- `/` — Open positions, strategy performance, profit chart, win-rate-by-league,
  step frequency and recent bets.
- `/analytics` — cumulative profit, stake vs profit, profit per strategy and
  profit per league.
- `/categories` — build league categories from every league the bot knows
  (collected from live markets plus every league that has appeared on a
  bet), grouped by sport. Strategies can then filter on a category instead
  of listing leagues one by one.
- `/strategies` — manage strategies (BetDEX fields: market type, totals,
  staking plan, flashscore, etc.), global risk rules, reset per-strategy state.
- `/logs` — tail the bot and skipped logs.
- `/league_skips` — leagues skipped because they're not in a strategy's allowed list.

Times are shown in Europe/Athens. It is installable as a PWA.
