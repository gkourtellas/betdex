"""Manage-strategies dashboard. No graphs, just add/edit/enable/remove.

    python dashboard.py

Then open http://localhost:8051 in your browser.
"""

import os
import json
import shutil
import sqlite3
import docker
from datetime import datetime
from flask import Flask, jsonify, render_template_string, request

STRATEGIES_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "strategies.json")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "bets.db")

app = Flask(__name__)


def _load_file():
    if not os.path.isfile(STRATEGIES_FILE):
        return {"global_risk_rules": {}, "strategies": []}
    with open(STRATEGIES_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_strategies_file():
    return _load_file().get("strategies", [])


def load_global_risk_rules():
    return _load_file().get("global_risk_rules", {})


def _validate_one(s):
    if not s.get("name"):
        return "Every strategy needs a name."

    if s.get("strategy_mode") == "multi_market":
        configs = s.get("market_configs", [])
        if not configs:
            return f"'{s['name']}': multi market strategy needs at least one market."
        for c in configs:
            if not c.get("market_type_id"):
                return f"'{s['name']}': every market row needs a market type id."
            if c.get("min_back_odds", 0) > c.get("max_back_odds", 0):
                return f"'{s['name']}': min odds > max odds in one of the market rows."
            if "OVER_UNDER" in c["market_type_id"] and not (c.get("total_range") and c.get("total_direction")):
                return f"'{s['name']}': Over/Under market row needs total_range and total_direction."
    else:
        if not s.get("market_type_id"):
            return f"'{s['name']}': missing market_type_id."
        if s.get("min_back_odds", 0) > s.get("max_back_odds", 0):
            return f"'{s['name']}': min_back_odds is greater than max_back_odds."
        if "OVER_UNDER" in s["market_type_id"]:
            has_exact = s.get("total_range") and s.get("total_direction")
            has_range = s.get("total_range_min") is not None and s.get("total_range_max") is not None and s.get("total_direction")
            if not has_exact and not has_range:
                return f"'{s['name']}': Over/Under market needs total_direction, plus an exact line or a min/max range."

    if s.get("strategy_type") == "compound":
        if not s.get("compound_start") or not s.get("compound_target"):
            return f"'{s['name']}': compound strategy needs start and target amount."
        if s.get("compound_target") <= s.get("compound_start"):
            return f"'{s['name']}': target must be greater than starting amount."
    else:
        plan = s.get("staking_plan") or []
        if not plan:
            return f"'{s['name']}': staking_plan must have at least one number."

    return None


def validate_strategies(strategies):
    if not isinstance(strategies, list):
        return "strategies must be a list."
    names = []
    for s in strategies:
        err = _validate_one(s)
        if err:
            return err
        names.append(s["name"])
    if len(names) != len(set(names)):
        return "Two strategies have the same name. Names must be unique."
    return None


def validate_global_risk_rules(rules):
    if not isinstance(rules, dict):
        return "global_risk_rules must be an object."
    for key in ("max_spread_pct", "minimum_liquidity"):
        if key in rules and rules[key] is not None:
            try:
                if float(rules[key]) < 0:
                    return f"'{key}' can't be negative."
            except (TypeError, ValueError):
                return f"'{key}' must be a number."
    return None


def _backup_current_file():
    if os.path.isfile(STRATEGIES_FILE):
        backup_dir = os.path.join(os.path.dirname(STRATEGIES_FILE), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy(STRATEGIES_FILE, os.path.join(backup_dir, f"strategies_{stamp}.json"))


def save_strategies_file(strategies):
    """Writes the strategies list, preserving whatever global_risk_rules
    is already on disk (this endpoint only ever touches strategies).
    """
    error = validate_strategies(strategies)
    if error:
        raise ValueError(error)

    global_rules = load_global_risk_rules()
    _backup_current_file()

    with open(STRATEGIES_FILE, "w", encoding="utf-8") as f:
        json.dump({"global_risk_rules": global_rules, "strategies": strategies}, f, indent=2)


def save_global_risk_rules(rules):
    """Writes global_risk_rules, preserving whatever strategies list is
    already on disk (this endpoint only ever touches global_risk_rules).
    """
    error = validate_global_risk_rules(rules)
    if error:
        raise ValueError(error)

    strategies = load_strategies_file()
    _backup_current_file()

    with open(STRATEGIES_FILE, "w", encoding="utf-8") as f:
        json.dump({"global_risk_rules": rules, "strategies": strategies}, f, indent=2)


def restart_bot_container():
    """Restarts the trading bot container by talking to the docker
    socket directly (python 'docker' package), instead of shelling out
    to a 'docker' CLI binary — that binary isn't guaranteed to be
    present even when the docker.io apt package is installed.
    """
    try:
        client = docker.from_env()
        container = client.containers.get("betdex_trading_bot")
        container.restart()
        return True, "Bot restarted."
    except docker.errors.NotFound:
        return False, "Container 'betdex_trading_bot' not found."
    except Exception as e:
        return False, f"Could not restart bot: {e}"


def _connect_db():
    if not os.path.isfile(DB_PATH):
        return None
    return sqlite3.connect(DB_PATH)


def get_stats():
    """Per-strategy totals + a running balance-over-time series, built
    from every settled bet in config/bets.db.
    """
    conn = _connect_db()
    if conn is None:
        return {"strategies": [], "timeline": []}

    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT strategy_name, event_name, selection_name, price, stake,
                  result, profit, settled_at
           FROM bets
           WHERE result IS NOT NULL
           ORDER BY settled_at ASC"""
    ).fetchall()
    conn.close()

    per_strategy = {}
    timeline = []
    running_total = 0.0

    for r in rows:
        name = r["strategy_name"]
        s = per_strategy.setdefault(name, {
            "name": name, "bets": 0, "wins": 0, "losses": 0, "profit": 0.0
        })
        s["bets"] += 1
        if r["result"] == "won":
            s["wins"] += 1
        else:
            s["losses"] += 1
        profit = r["profit"] or 0.0
        s["profit"] = round(s["profit"] + profit, 4)

        running_total = round(running_total + profit, 4)
        timeline.append({
            "settled_at": r["settled_at"],
            "strategy_name": name,
            "event_name": r["event_name"],
            "result": r["result"],
            "profit": profit,
            "running_total": running_total,
        })

    strategies_out = []
    for s in per_strategy.values():
        win_rate = round(100 * s["wins"] / s["bets"], 1) if s["bets"] else 0
        strategies_out.append({**s, "win_rate": win_rate})
    strategies_out.sort(key=lambda x: x["profit"], reverse=True)

    return {"strategies": strategies_out, "timeline": timeline}


PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BetDEX Strategies</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.4/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0a0e14; --card: #11161f; --card2: #141a25;
    --border: #1c2330; --text: #e4e7ec; --muted: #7a8699;
    --win: #2dd4a8; --loss: #ff6b5e; --accent: #5b8def;
  }
  * { box-sizing: border-box; }
  body { font-family: 'Inter', -apple-system, Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 28px 32px 60px; }
  .topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; }
  .topbar h0 { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--muted); letter-spacing: 0.12em; text-transform: uppercase; }
  .topbar-actions { display: flex; gap: 10px; }
  .nav-btn { font-family: 'Inter', sans-serif; font-size: 13px; font-weight: 600; color: var(--bg); background: var(--accent); border: none; border-radius: 8px; padding: 9px 16px; cursor: pointer; }
  .tabs { display: flex; gap: 6px; margin-bottom: 18px; }
  .tab { font-family: 'JetBrains Mono', monospace; font-size: 12.5px; padding: 8px 14px; border-radius: 7px; border: 1px solid var(--border); background: var(--card2); color: var(--muted); cursor: pointer; }
  .tab.active { color: var(--text); border-color: var(--accent); }
  h1 { font-size: 14px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); margin: 30px 0 12px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; }
  .strat-row { display: flex; justify-content: space-between; align-items: center; }
  .strat-name { font-weight: 600; font-size: 14.5px; }
  .strat-meta { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--muted); margin-top: 4px; }
  .badge { font-family: 'JetBrains Mono', monospace; font-size: 11px; padding: 3px 8px; border-radius: 4px; margin-right: 8px; }
  .badge.on { background: rgba(45,212,168,0.15); color: var(--win); }
  .badge.off { background: rgba(122,134,153,0.15); color: var(--muted); }
  .row-actions { display: flex; gap: 8px; }
  .btn { font-family: 'JetBrains Mono', monospace; font-size: 12px; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--card2); color: var(--text); cursor: pointer; }
  .btn.danger:hover { border-color: var(--loss); color: var(--loss); }
  .add-btn { font-family: 'JetBrains Mono', monospace; font-size: 13px; padding: 10px 16px; border-radius: 8px; border: 1px dashed var(--border); background: transparent; color: var(--muted); cursor: pointer; width: 100%; margin-top: 6px; }

  .stat-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
  .stat-card .label { font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--muted); text-transform: uppercase; }
  .stat-card .value { font-family: 'JetBrains Mono', monospace; font-size: 22px; font-weight: 700; margin-top: 6px; }
  .value.pos { color: var(--win); }
  .value.neg { color: var(--loss); }
  .chart-wrap { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 18px; margin-bottom: 20px; height: 320px; }
  table.strat-table { width: 100%; border-collapse: collapse; font-family: 'JetBrains Mono', monospace; font-size: 12.5px; }
  table.strat-table th, table.strat-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
  table.strat-table th { color: var(--muted); font-weight: 500; text-transform: uppercase; font-size: 11px; }
  .empty-note { color: var(--muted); font-family: 'JetBrains Mono', monospace; font-size: 13px; }

  .risk-card { display: flex; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
  .risk-field label { display: block; font-size: 11.5px; color: var(--muted); margin-bottom: 5px; font-family: 'JetBrains Mono', monospace; }
  .risk-field input { width: 160px; background: var(--card2); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 8px 10px; font-family: 'JetBrains Mono', monospace; font-size: 13px; }
  .risk-hint { color: var(--muted); font-family: 'JetBrains Mono', monospace; font-size: 11.5px; max-width: 340px; }
  .risk-saved { color: var(--win); font-family: 'JetBrains Mono', monospace; font-size: 12px; display: none; }

  .modal-bg { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6); align-items: center; justify-content: center; z-index: 10; }
  .modal-bg.open { display: flex; }
  .modal { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; width: 640px; max-height: 85vh; overflow-y: auto; }
  .field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .field { margin-bottom: 12px; }
  .field.full { grid-column: 1 / -1; }
  .field label { display: block; font-size: 11.5px; color: var(--muted); margin-bottom: 5px; font-family: 'JetBrains Mono', monospace; }
  .field input, .field select { width: 100%; background: var(--card2); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 8px 10px; font-family: 'JetBrains Mono', monospace; font-size: 13px; }
  .field .subhint { color: var(--muted); font-size: 10.5px; margin-top: 3px; }
  .checkbox-row { display: flex; align-items: center; gap: 8px; }
  .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
  .error-box { background: rgba(255,107,94,0.12); border: 1px solid var(--loss); color: var(--loss); padding: 10px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 14px; display: none; }
  .market-row { background: var(--card2); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; position: relative; }
  .saving-banner { display: none; background: rgba(245,185,66,0.12); border: 1px solid var(--pending, #f5b942); color: #f5b942; padding: 10px 14px; border-radius: 8px; font-family: 'JetBrains Mono', monospace; font-size: 13px; margin-bottom: 16px; }
</style>
</head>
<body>
  <div class="topbar">
    <h0>betdex // dashboard</h0>
    <div class="topbar-actions">
      <button class="nav-btn" style="background:#f5b942; color:#1a1300;" onclick="restartBot()">⟲ Restart Bot</button>
    </div>
  </div>

  <div class="tabs">
    <div class="tab active" id="tab_stats" onclick="showTab('stats')">Stats</div>
    <div class="tab" id="tab_strategies" onclick="showTab('strategies')">Strategies</div>
  </div>

  <div class="saving-banner" id="restartBanner">Restarting the bot…</div>

  <div id="view_stats">
    <div class="stat-cards" id="statCards"></div>
    <div class="chart-wrap"><canvas id="balanceChart"></canvas></div>
    <h1>Per Strategy</h1>
    <div id="strategyTableWrap"></div>
  </div>

  <div id="view_strategies" style="display:none;">
    <h1>Global Risk Rules</h1>
    <div class="card risk-card">
      <div class="risk-field">
        <label>Max back/lay spread (%)</label>
        <input id="g_max_spread_pct" type="number" step="0.1" placeholder="e.g. 8">
      </div>
      <div class="risk-field">
        <label>Min liquidity (both sides)</label>
        <input id="g_min_liquidity" type="number" step="0.01" placeholder="e.g. 2">
      </div>
      <button class="btn" style="border-color: var(--accent); color: var(--accent);" onclick="saveGlobalRiskRules()">Save</button>
      <span class="risk-saved" id="riskSaved">Saved.</span>
      <div class="risk-hint">Applies to every strategy that doesn't set its own value below. A strategy's own "Max spread %" / "Minimum liquidity" always overrides these.</div>
    </div>

    <h1>Strategies</h1>
    <div id="strategyList"></div>
    <button class="add-btn" onclick="openModal(null)">+ Add strategy</button>
  </div>

  <div class="modal-bg" id="modalBg">
    <div class="modal">
      <h2 id="modalTitle">Add Strategy</h2>
      <div class="error-box" id="errorBox"></div>
      <div class="field-grid">
        <div class="field full"><label>Name</label><input id="f_name"></div>
        <div class="field full">
          <div class="checkbox-row"><input type="checkbox" id="f_multi" onchange="onMultiToggle()"><label style="margin:0;">Multi-market (bet across more than one market type)</label></div>
        </div>

        <div id="single_fields" class="field full">
          <div class="field-grid">
            <div class="field"><label>Sport</label><select id="f_sport" onchange="onSportChange('f_sport','f_market_type')"></select></div>
            <div class="field"><label>Market type</label><select id="f_market_type"></select></div>
            <div class="field"><label>Min back odds</label><input id="f_min_odds" type="number" step="0.01"></div>
            <div class="field"><label>Max back odds</label><input id="f_max_odds" type="number" step="0.01"></div>
            <div class="field"><label>Total line (exact, e.g. 2.5)</label><input id="f_total_range" placeholder="e.g. 2.5"></div>
            <div class="field"><label>Line range min (optional, e.g. 8.5)</label><input id="f_total_range_min" type="number" step="0.5" placeholder="e.g. 8.5"></div>
            <div class="field"><label>Line range max (optional, e.g. 10.5)</label><input id="f_total_range_max" type="number" step="0.5" placeholder="e.g. 10.5"></div>
            <div class="field">
              <label>Direction (Over/Under only)</label>
              <select id="f_total_direction">
                <option value="">— not Over/Under —</option>
                <option value="Over">Over</option>
                <option value="Under">Under</option>
              </select>
            </div>
            <div class="field">
              <label>Max spread % (blank = use global)</label>
              <input id="f_max_spread_pct" type="number" step="0.1" placeholder="e.g. 8">
            </div>
            <div class="field">
              <label>Min liquidity (blank = use global)</label>
              <input id="f_min_liquidity_strat" type="number" step="0.01" placeholder="e.g. 2">
              <div class="subhint">Required on BOTH back and lay side.</div>
            </div>
          </div>
        </div>

        <div id="multi_fields" class="field full" style="display:none;">
          <label>Market rows</label>
          <div id="market_rows"></div>
          <button class="btn" onclick="addMarketRow()" style="margin-top:6px; font-size:12px;">+ Add market</button>
        </div>

        <div class="field full">
          <label>Strategy type</label>
          <select id="f_strategy_type" onchange="onStrategyTypeChange()">
            <option value="normal">Normal (staking plan)</option>
            <option value="compound">Compound (all-in, compounding)</option>
          </select>
        </div>
        <div id="compound_fields" style="display:none;">
          <div class="field"><label>Starting amount</label><input id="f_compound_start" type="number" step="0.01"></div>
          <div class="field"><label>Target amount</label><input id="f_compound_target" type="number" step="0.01"></div>
        </div>
        <div id="normal_fields">
          <div class="field full"><label>Staking plan (comma-separated)</label><input id="f_staking_plan" placeholder="0.1, 0.3, 0.9, 2.7, 8.1, 24.3"></div>
        </div>

        <div class="field">
          <label>Timing</label>
          <select id="f_live_mode">
            <option value="pre">Pre-match only</option>
            <option value="live">Live only</option>
            <option value="both">Both</option>
          </select>
        </div>
        <div class="field"><label>Max open bets</label><input id="f_max_open_bets" type="number"></div>
        <div class="field"><label>Poll interval (seconds)</label><input id="f_poll_interval" type="number"></div>
        <div class="field"><label>Cooldown after bet (seconds)</label><input id="f_cooldown" type="number"></div>
        <div class="field"><label>Lookahead (minutes)</label><input id="f_lookahead" type="number"></div>
        <div class="field"><label>Min seconds to start</label><input id="f_min_seconds" type="number"></div>
        <div class="field full">
          <div class="checkbox-row"><input type="checkbox" id="f_enabled"><label style="margin:0;">Enabled</label></div>
        </div>
      </div>
      <div class="modal-actions">
        <button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn" style="border-color: var(--accent); color: var(--accent);" onclick="saveStrategy()">Save</button>
      </div>
    </div>
  </div>

<script>
let strategies = [];
let editingIndex = null;
let marketRowCount = 0;
let balanceChart = null;

const SPORTS_MARKETS = {
  "Football": [
    ["FOOTBALL_FULL_TIME_RESULT", "Full Time Result (1X2)"],
    ["FOOTBALL_OVER_UNDER_TOTAL_GOALS", "Total Goals Over/Under"],
    ["FOOTBALL_CORNERS_OVER_UNDER", "Corners Over/Under"],
    ["FOOTBALL_BOTH_TEAMS_TO_SCORE", "Both Teams to Score"],
    ["FOOTBALL_HALF_TIME_RESULT", "Half Time Result"],
    ["FOOTBALL_DOUBLE_CHANCE", "Double Chance"],
  ],
  "Basketball": [
    ["BASKETBALL_FULL_TIME_RESULT", "Full Time Result (Moneyline)"],
    ["BASKETBALL_OVER_UNDER_TOTAL_POINTS", "Total Points Over/Under"],
    ["BASKETBALL_HANDICAP", "Handicap"],
  ],
  "Tennis": [
    ["TENNIS_MATCH_WINNER", "Match Winner"],
    ["TENNIS_OVER_UNDER_TOTAL_GAMES", "Total Games Over/Under"],
    ["TENNIS_SET_HANDICAP", "Set Handicap"],
  ],
  "Ice Hockey": [
    ["ICE_HOCKEY_FULL_TIME_RESULT", "Full Time Result"],
    ["ICE_HOCKEY_OVER_UNDER_TOTAL_GOALS", "Total Goals Over/Under"],
  ],
  "American Football": [
    ["AMERICAN_FOOTBALL_FULL_TIME_RESULT", "Full Time Result (Moneyline)"],
    ["AMERICAN_FOOTBALL_OVER_UNDER_TOTAL_POINTS", "Total Points Over/Under"],
    ["AMERICAN_FOOTBALL_HANDICAP", "Handicap"],
  ],
  "Baseball": [
    ["BASEBALL_FULL_TIME_RESULT", "Full Time Result (Moneyline)"],
    ["BASEBALL_OVER_UNDER_TOTAL_RUNS", "Total Runs Over/Under"],
  ],
};

function populateSportSelect(sportSelectId) {
  const sel = document.getElementById(sportSelectId);
  sel.innerHTML = Object.keys(SPORTS_MARKETS).map(s => `<option value="${s}">${s}</option>`).join('');
}

function populateMarketSelect(sportSelectId, marketSelectId) {
  const sport = document.getElementById(sportSelectId).value;
  const marketSel = document.getElementById(marketSelectId);
  const opts = SPORTS_MARKETS[sport] || [];
  marketSel.innerHTML = opts.map(([id, label]) => `<option value="${id}">${label}</option>`).join('');
}

function onSportChange(sportSelectId, marketSelectId) {
  populateMarketSelect(sportSelectId, marketSelectId);
}

function setSportMarketFromType(sportSelectId, marketSelectId, marketTypeId) {
  let sportFound = Object.keys(SPORTS_MARKETS)[0];
  for (const [sport, list] of Object.entries(SPORTS_MARKETS)) {
    if (list.some(([id]) => id === marketTypeId)) { sportFound = sport; break; }
  }
  document.getElementById(sportSelectId).value = sportFound;
  populateMarketSelect(sportSelectId, marketSelectId);
  if (marketTypeId) document.getElementById(marketSelectId).value = marketTypeId;
}

function showTab(tab) {
  document.getElementById('view_stats').style.display = tab === 'stats' ? '' : 'none';
  document.getElementById('view_strategies').style.display = tab === 'strategies' ? '' : 'none';
  document.getElementById('tab_stats').classList.toggle('active', tab === 'stats');
  document.getElementById('tab_strategies').classList.toggle('active', tab === 'strategies');
  if (tab === 'stats') fetchStats();
  if (tab === 'strategies') fetchGlobalRiskRules();
}

function fmtMoney(n) {
  const sign = n > 0 ? '+' : '';
  return sign + n.toFixed(2);
}

function fetchStats() {
  fetch('/api/stats').then(r => r.json()).then(renderStats);
}

function renderStats(data) {
  const strategies = data.strategies || [];
  const timeline = data.timeline || [];

  const totalBets = strategies.reduce((a, s) => a + s.bets, 0);
  const totalWins = strategies.reduce((a, s) => a + s.wins, 0);
  const totalProfit = strategies.reduce((a, s) => a + s.profit, 0);
  const winRate = totalBets ? (100 * totalWins / totalBets).toFixed(1) : '0.0';

  document.getElementById('statCards').innerHTML = `
    <div class="stat-card"><div class="label">Total Bets</div><div class="value">${totalBets}</div></div>
    <div class="stat-card"><div class="label">Win Rate</div><div class="value">${winRate}%</div></div>
    <div class="stat-card"><div class="label">Total Profit</div><div class="value ${totalProfit >= 0 ? 'pos' : 'neg'}">${fmtMoney(totalProfit)}</div></div>
    <div class="stat-card"><div class="label">Strategies Tracked</div><div class="value">${strategies.length}</div></div>
  `;

  if (!strategies.length) {
    document.getElementById('strategyTableWrap').innerHTML = '<p class="empty-note">No settled bets yet.</p>';
  } else {
    let rows = strategies.map(s => `
      <tr>
        <td>${s.name}</td>
        <td>${s.bets}</td>
        <td>${s.wins}</td>
        <td>${s.losses}</td>
        <td>${s.win_rate}%</td>
        <td style="color: ${s.profit >= 0 ? 'var(--win)' : 'var(--loss)'}">${fmtMoney(s.profit)}</td>
      </tr>`).join('');
    document.getElementById('strategyTableWrap').innerHTML = `
      <table class="strat-table">
        <thead><tr><th>Strategy</th><th>Bets</th><th>Wins</th><th>Losses</th><th>Win Rate</th><th>Profit</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  const labels = timeline.map(t => t.settled_at ? t.settled_at.slice(0, 16).replace('T', ' ') : '');
  const values = timeline.map(t => t.running_total);

  const ctx = document.getElementById('balanceChart').getContext('2d');
  if (balanceChart) balanceChart.destroy();
  balanceChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: 'Running Profit',
        data: values,
        borderColor: '#5b8def',
        backgroundColor: 'rgba(91,141,239,0.12)',
        fill: true,
        tension: 0.15,
        pointRadius: 0,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#7a8699', maxTicksLimit: 8 }, grid: { color: '#1c2330' } },
        y: { ticks: { color: '#7a8699' }, grid: { color: '#1c2330' } },
      }
    }
  });
}

function fetchGlobalRiskRules() {
  fetch('/api/global_risk_rules').then(r => r.json()).then(rules => {
    document.getElementById('g_max_spread_pct').value = rules.max_spread_pct ?? '';
    document.getElementById('g_min_liquidity').value = rules.minimum_liquidity ?? '';
  });
}

function saveGlobalRiskRules() {
  const maxSpreadRaw = document.getElementById('g_max_spread_pct').value.trim();
  const minLiqRaw = document.getElementById('g_min_liquidity').value.trim();
  const rules = {
    max_spread_pct: maxSpreadRaw === '' ? null : parseFloat(maxSpreadRaw),
    minimum_liquidity: minLiqRaw === '' ? null : parseFloat(minLiqRaw),
  };
  fetch('/api/global_risk_rules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rules)
  }).then(r => r.json()).then(result => {
    if (result.error) { alert('Could not save: ' + result.error); return; }
    const saved = document.getElementById('riskSaved');
    saved.style.display = 'inline';
    setTimeout(() => { saved.style.display = 'none'; }, 2000);
  }).catch(err => alert('Save failed: ' + err));
}

function fetchStrategies() {
  fetch('/api/strategies').then(r => r.json()).then(data => {
    strategies = Array.isArray(data) ? data : [];
    renderList();
  });
}

function renderList() {
  let html = '';
  strategies.forEach((s, i) => {
    const badgeClass = s.enabled ? 'on' : 'off';
    const badgeText = s.enabled ? 'ACTIVE' : 'INACTIVE';
    let metaLine;
    if (s.strategy_mode === 'multi_market') {
      metaLine = 'MULTI: ' + (s.market_configs || []).map(c => c.market_type_id).join(', ');
    } else {
      metaLine = `${s.market_type_id} · odds ${s.min_back_odds}-${s.max_back_odds}`;
      if (s.total_direction) metaLine += ` · ${s.total_direction} ${s.total_range}`;
    }
    if (s.strategy_type === 'compound') {
      metaLine += ` · compound ${s.compound_start} -> ${s.compound_target}`;
    }
    metaLine += ` · ${s.live_mode || 'pre'}`;
    metaLine += ` · spread ${s.max_spread_pct != null ? s.max_spread_pct + '%' : 'global'}`;
    metaLine += ` · liq ${s.minimum_liquidity != null ? s.minimum_liquidity : 'global'}`;

    html += `<div class="card strat-row">
      <div>
        <div class="strat-name"><span class="badge ${badgeClass}">${badgeText}</span>${s.name}</div>
        <div class="strat-meta">${metaLine}</div>
      </div>
      <div class="row-actions">
        <button class="btn" onclick="openModal(${i})">Edit</button>
        <button class="btn" onclick="toggleActive(${i})">${s.enabled ? 'Disable' : 'Enable'}</button>
        <button class="btn danger" onclick="removeStrategy(${i})">Remove</button>
      </div>
    </div>`;
  });
  document.getElementById('strategyList').innerHTML = html || '<p style="color:var(--muted); font-family:JetBrains Mono, monospace; font-size:13px;">No strategies yet.</p>';
}

const MARKET_ROW_TEMPLATE = (idx, data={}) => `
  <div class="market-row" id="market_row_${idx}">
    <button onclick="removeMarketRow(${idx})" style="position:absolute;top:8px;right:10px;background:none;border:none;color:var(--muted);cursor:pointer;">✕</button>
    <div class="field-grid" style="grid-template-columns:1fr 1fr;">
      <div class="field"><label>Sport</label><select class="mr_sport" id="mr_sport_${idx}" onchange="onSportChange('mr_sport_${idx}','mr_type_${idx}')"></select></div>
      <div class="field"><label>Market type</label><select class="mr_type" id="mr_type_${idx}"></select></div>
      <div class="field"><label>Min odds</label><input class="mr_min" type="number" step="0.01" value="${data.min_back_odds??1.45}"></div>
      <div class="field"><label>Max odds</label><input class="mr_max" type="number" step="0.01" value="${data.max_back_odds??1.6}"></div>
      <div class="field"><label>Total line</label><input class="mr_range" value="${data.total_range||''}" placeholder="e.g. 2.5"></div>
      <div class="field">
        <label>Direction</label>
        <select class="mr_dir">
          <option value="" ${!data.total_direction?'selected':''}>— none —</option>
          <option value="Over" ${data.total_direction==='Over'?'selected':''}>Over</option>
          <option value="Under" ${data.total_direction==='Under'?'selected':''}>Under</option>
        </select>
      </div>
      <div class="field"><label>Max spread % (blank = global)</label><input class="mr_spread" type="number" step="0.1" value="${data.max_spread_pct??''}" placeholder="e.g. 8"></div>
      <div class="field"><label>Min liquidity (blank = global)</label><input class="mr_liq" type="number" step="0.01" value="${data.minimum_liquidity??''}" placeholder="e.g. 2"></div>
    </div>
  </div>`;

function addMarketRow(data={}) {
  const idx = marketRowCount++;
  const div = document.createElement('div');
  div.innerHTML = MARKET_ROW_TEMPLATE(idx, data);
  document.getElementById('market_rows').appendChild(div.firstElementChild);
  populateSportSelect('mr_sport_' + idx);
  setSportMarketFromType('mr_sport_' + idx, 'mr_type_' + idx, data.market_type_id || '');
}

function removeMarketRow(idx) {
  const el = document.getElementById('market_row_' + idx);
  if (el) el.remove();
}

function getMarketRows() {
  return Array.from(document.querySelectorAll('#market_rows .market-row')).map(row => {
    const spreadRaw = row.querySelector('.mr_spread').value.trim();
    const liqRaw = row.querySelector('.mr_liq').value.trim();
    return {
      market_type_id: row.querySelector('.mr_type').value.trim(),
      min_back_odds: parseFloat(row.querySelector('.mr_min').value),
      max_back_odds: parseFloat(row.querySelector('.mr_max').value),
      total_range: row.querySelector('.mr_range').value.trim() || null,
      total_direction: row.querySelector('.mr_dir').value || null,
      max_spread_pct: spreadRaw === '' ? null : parseFloat(spreadRaw),
      minimum_liquidity: liqRaw === '' ? null : parseFloat(liqRaw),
    };
  });
}

function onMultiToggle() {
  const isMulti = document.getElementById('f_multi').checked;
  document.getElementById('single_fields').style.display = isMulti ? 'none' : '';
  document.getElementById('multi_fields').style.display = isMulti ? '' : 'none';
}

function onStrategyTypeChange() {
  const isCompound = document.getElementById('f_strategy_type').value === 'compound';
  document.getElementById('compound_fields').style.display = isCompound ? '' : 'none';
  document.getElementById('normal_fields').style.display = isCompound ? 'none' : '';
}

function openModal(index) {
  editingIndex = index;
  document.getElementById('errorBox').style.display = 'none';
  document.getElementById('market_rows').innerHTML = '';
  marketRowCount = 0;
  const s = index === null ? {} : strategies[index];
  document.getElementById('modalTitle').textContent = index === null ? 'Add Strategy' : `Edit: ${s.name}`;
  document.getElementById('f_name').value = s.name || '';

  const isMulti = s.strategy_mode === 'multi_market';
  document.getElementById('f_multi').checked = isMulti;
  onMultiToggle();

  populateSportSelect('f_sport');
  setSportMarketFromType('f_sport', 'f_market_type', s.market_type_id || '');

  if (isMulti) {
    (s.market_configs || [{}]).forEach(c => addMarketRow(c));
  } else {
    addMarketRow();
    document.getElementById('f_min_odds').value = s.min_back_odds ?? 1.45;
    document.getElementById('f_max_odds').value = s.max_back_odds ?? 1.6;
    document.getElementById('f_total_range').value = s.total_range ?? '';
    document.getElementById('f_total_range_min').value = s.total_range_min ?? '';
    document.getElementById('f_total_range_max').value = s.total_range_max ?? '';
    document.getElementById('f_total_direction').value = s.total_direction ?? '';
    document.getElementById('f_max_spread_pct').value = s.max_spread_pct ?? '';
    document.getElementById('f_min_liquidity_strat').value = s.minimum_liquidity ?? '';
  }

  const stratType = s.strategy_type || 'normal';
  document.getElementById('f_strategy_type').value = stratType;
  document.getElementById('f_staking_plan').value = (s.staking_plan || [0.1]).join(', ');
  document.getElementById('f_compound_start').value = s.compound_start ?? '';
  document.getElementById('f_compound_target').value = s.compound_target ?? '';
  onStrategyTypeChange();

  document.getElementById('f_live_mode').value = s.live_mode || 'pre';
  document.getElementById('f_max_open_bets').value = s.max_open_bets ?? 1;
  document.getElementById('f_poll_interval').value = s.poll_interval_seconds ?? 600;
  document.getElementById('f_cooldown').value = s.open_positions_cooldown_seconds ?? 600;
  document.getElementById('f_lookahead').value = s.event_lookahead_minutes ?? 180;
  document.getElementById('f_min_seconds').value = s.min_seconds_to_start ?? 300;
  document.getElementById('f_enabled').checked = s.enabled !== false;

  document.getElementById('modalBg').classList.add('open');
}

function closeModal() {
  document.getElementById('modalBg').classList.remove('open');
}

function showError(msg) {
  const box = document.getElementById('errorBox');
  box.textContent = msg;
  box.style.display = 'block';
}

function saveStrategy() {
  const name = document.getElementById('f_name').value.trim();
  if (!name) { showError('Name is required.'); return; }

  const isMulti = document.getElementById('f_multi').checked;
  const stratType = document.getElementById('f_strategy_type').value;
  const existing = editingIndex === null ? {} : strategies[editingIndex];

  let plan = null, compoundStart = null, compoundTarget = null;
  if (stratType === 'compound') {
    compoundStart = parseFloat(document.getElementById('f_compound_start').value);
    compoundTarget = parseFloat(document.getElementById('f_compound_target').value);
    if (!compoundStart || !compoundTarget) { showError('Compound strategy needs starting and target amount.'); return; }
    if (compoundTarget <= compoundStart) { showError('Target must be greater than starting amount.'); return; }
  } else {
    plan = document.getElementById('f_staking_plan').value.split(',').map(x => parseFloat(x.trim())).filter(x => !isNaN(x));
    if (!plan.length) { showError('Staking plan must have at least one number.'); return; }
  }

  const updated = {
    ...existing,
    name,
    strategy_mode: isMulti ? 'multi_market' : 'single',
    strategy_type: stratType,
    staking_plan: plan,
    staking_steps: plan ? plan.length : null,
    compound_start: compoundStart,
    compound_target: compoundTarget,
    live_mode: document.getElementById('f_live_mode').value,
    max_open_bets: parseInt(document.getElementById('f_max_open_bets').value) || 1,
    poll_interval_seconds: parseInt(document.getElementById('f_poll_interval').value) || 600,
    open_positions_cooldown_seconds: parseInt(document.getElementById('f_cooldown').value) || 600,
    event_lookahead_minutes: parseInt(document.getElementById('f_lookahead').value) || 180,
    min_seconds_to_start: parseInt(document.getElementById('f_min_seconds').value) || 300,
    keep_in_play: existing.keep_in_play ?? (document.getElementById('f_live_mode').value !== 'pre'),
    enabled: document.getElementById('f_enabled').checked,
  };

  if (isMulti) {
    const rows = getMarketRows();
    if (!rows.length) { showError('Add at least one market row.'); return; }
    for (const r of rows) {
      if (!r.market_type_id) { showError('Every market row needs a market type id.'); return; }
      if (r.min_back_odds > r.max_back_odds) { showError('Min odds > max odds in a market row.'); return; }
      if (r.market_type_id.includes('OVER_UNDER') && (!r.total_range || !r.total_direction)) {
        showError(`Over/Under row (${r.market_type_id}) needs total line + direction.`); return;
      }
    }
    updated.market_configs = rows;
    updated.market_type_id = rows[0].market_type_id;
    updated.min_back_odds = rows[0].min_back_odds;
    updated.max_back_odds = rows[0].max_back_odds;
    updated.total_range = null;
    updated.total_direction = null;
    updated.max_spread_pct = null;
    updated.minimum_liquidity = null;
  } else {
    const marketType = document.getElementById('f_market_type').value.trim();
    const minOdds = parseFloat(document.getElementById('f_min_odds').value);
    const maxOdds = parseFloat(document.getElementById('f_max_odds').value);
    const totalRange = document.getElementById('f_total_range').value.trim();
    const totalRangeMin = document.getElementById('f_total_range_min').value.trim();
    const totalRangeMax = document.getElementById('f_total_range_max').value.trim();
    const totalDirection = document.getElementById('f_total_direction').value;
    const maxSpreadRaw = document.getElementById('f_max_spread_pct').value.trim();
    const minLiqRaw = document.getElementById('f_min_liquidity_strat').value.trim();

    if (!marketType) { showError('Market type id is required.'); return; }
    if (minOdds > maxOdds) { showError('Min odds > max odds.'); return; }
    const isOverUnder = marketType.includes('OVER_UNDER');
    const hasExactLine = totalRange && totalDirection;
    const hasRangeLine = totalRangeMin !== '' && totalRangeMax !== '' && totalDirection;
    if (isOverUnder && !hasExactLine && !hasRangeLine) {
      showError('Over/Under market needs a direction, plus either an exact line or a min/max range.'); return;
    }

    updated.market_configs = null;
    updated.market_type_id = marketType;
    updated.min_back_odds = minOdds;
    updated.max_back_odds = maxOdds;
    updated.total_range = isOverUnder && !hasRangeLine ? totalRange : null;
    updated.total_range_min = isOverUnder && hasRangeLine ? parseFloat(totalRangeMin) : null;
    updated.total_range_max = isOverUnder && hasRangeLine ? parseFloat(totalRangeMax) : null;
    updated.total_direction = isOverUnder ? totalDirection : null;
    updated.max_spread_pct = maxSpreadRaw === '' ? null : parseFloat(maxSpreadRaw);
    updated.minimum_liquidity = minLiqRaw === '' ? null : parseFloat(minLiqRaw);
  }

  if (editingIndex === null) {
    strategies.push(updated);
  } else {
    strategies[editingIndex] = updated;
  }
  persist();
}

function toggleActive(index) {
  strategies[index].enabled = !strategies[index].enabled;
  persist();
}

function removeStrategy(index) {
  if (!confirm(`Remove strategy "${strategies[index].name}"? A backup is kept.`)) return;
  strategies.splice(index, 1);
  persist();
}

function persist() {
  fetch('/api/strategies', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(strategies)
  }).then(r => r.json()).then(result => {
    if (result.error) { showError(result.error); return; }
    closeModal();
    fetchStrategies();
  }).catch(err => showError('Save failed: ' + err));
}

function restartBot() {
  document.getElementById('restartBanner').style.display = 'block';
  fetch('/api/restart_bot', { method: 'POST' })
    .then(r => r.json())
    .then(result => {
      document.getElementById('restartBanner').style.display = 'none';
      alert(result.restarted ? 'Bot restarted.' : ('Restart failed: ' + result.message));
    })
    .catch(err => {
      document.getElementById('restartBanner').style.display = 'none';
      alert('Restart failed: ' + err);
    });
}

fetchStrategies();
fetchStats();
fetchGlobalRiskRules();
</script>
</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(PAGE)


@app.route("/api/strategies", methods=["GET"])
def get_strategies():
    try:
        return jsonify(load_strategies_file())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/strategies", methods=["POST"])
def save_strategies():
    strategies = request.get_json(force=True)
    try:
        save_strategies_file(strategies)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Could not save: {e}"}), 500
    return jsonify({"saved": True})


@app.route("/api/global_risk_rules", methods=["GET"])
def get_global_risk_rules():
    try:
        return jsonify(load_global_risk_rules())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/global_risk_rules", methods=["POST"])
def save_global_risk_rules_route():
    rules = request.get_json(force=True)
    try:
        save_global_risk_rules(rules)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Could not save: {e}"}), 500
    return jsonify({"saved": True})


@app.route("/api/stats", methods=["GET"])
def stats():
    try:
        return jsonify(get_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/restart_bot", methods=["POST"])
def restart_bot():
    ok, msg = restart_bot_container()
    return jsonify({"restarted": ok, "message": msg})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8051)
