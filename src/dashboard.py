"""BetDEX dashboard. Ported from the Matchbook dashboard — same pages and
look/feel, running on BetDEX's own data and strategy fields.

    python dashboard.py

Then open http://localhost:8051 in your browser.
Reads config/bets.db — does not touch the bot or place any bets.

Set DASHBOARD_PASSWORD in your .env to require a password when accessed
from outside (e.g. through a tunnel).
"""

import os
import re
import json
import shutil
import sqlite3
import base64
import time
import threading
import docker
from datetime import datetime
from zoneinfo import ZoneInfo
from functools import wraps
from datetime import timedelta
from flask import Flask, jsonify, render_template_string, request, Response, send_from_directory, redirect, url_for, session

from state_store import _safe_filename
from api_client import BetdexClient

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "bets.db")
STRATEGIES_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "strategies.json")
CATEGORIES_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "league_categories.json")
LEAGUES_SEEN_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "leagues_seen.json")
STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "config", "state")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")
BOT_CONTAINER = "betdex_trading_bot"

# All timestamps are stored as naive UTC. The dashboard shows them in Athens
# time, so per-day grouping has to use the same zone or days will not line up
# with what the user sees elsewhere.
UTC_TZ = ZoneInfo("UTC")
ATHENS_TZ = ZoneInfo("Europe/Athens")

app = Flask(__name__)
app.secret_key = os.environ.get("DASHBOARD_SECRET_KEY", "change-me-please")
app.permanent_session_lifetime = timedelta(days=30)

BUILD_VERSION = "v18"

# BetDEX wallet balance is cached server-side: the exchange wants a session
# for every read, so polling it on each page load would log in constantly.
BALANCE_TTL = 60
_balance_cache = {"data": None, "at": 0.0}
_balance_lock = threading.Lock()


@app.route("/api/build_version")
def api_build_version():
    return jsonify({"build": BUILD_VERSION})


PWA_MANIFEST = {
    "name": "BetDEX Trading Bot",
    "short_name": "BetDEX",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#0a0e14",
    "theme_color": "#0a0e14",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}


@app.route("/manifest.json")
def manifest():
    return jsonify(PWA_MANIFEST)


@app.route("/static/<path:filename>")
def static_files(filename):
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    return send_from_directory(static_dir, filename)

ICON_192_B64 = "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"
ICON_512_B64 = "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAIAAAB7GkOtAAAWZElEQVR4nO3d6ZNdZZ3A8dtL9pCQjYQsJN0BBGQPCVOupc6MOmqpuKCo478xL6bK8sW8nr/BURE3GB0d1BpmXEpLCUtY3IB0OiGEhEB2svYyL7oqpkJCuvve5zzPc36fzysKiuc8OQ2/b597zz23b+GSlR0A4unPvQEA8hAAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACCowdwbKNGnv/Zi7i0AvffI12/IvYWy9C1csjL3HnIy6yGy4EmIGABDH3irgDEIFABzH5imIDFofwDMfWDW2l2CNgfA6Ad6oq0ZaGEAzH0ghfZloFUBMPqBBrSmBC0JgNEPNKwFGag+AEY/kEvtDag7AKY/kF29Gag1AEY/UJQaM1Dlw+BMf6A0Nc6lyq4AajzFQCgVXQrUdAVg+gPlq2hSVROAis4pEFwt86qCl4BqOZUAFyn85aDSA5Br+hf+YwNmyjB5q6ID0NgPrOSfEJBCkz0odsKUG4AGfjzF/lSAJoWdNoUGIOnPo8yfBJBdtMlTYgAS/QwKPPtAmYJMoeICkOK8l3bSgSq0fhyVFYCen+6izjVQoxbPpYIC0NuzXM4pBlqglQOqlAD08OQWcmaBlmlfA4oIgOkP1KJN86qaZwFNR/azCbRem+ZM/iuAnuS0TT8SoAotmF2ZrwBacAaBmHoyefI+7DJnAEx/oGq1N6Du9wBMfyCvqqdQtgB0H72qzzvQGt3PolwXAXkCYPoDbVJpA6p8Ccj0B0pT41zKEIAuQ1fjWQYi6HI6NX8R0HQATH+gxeqaUTW9BFTXmQVi6mZSNXwR0GgAuvmzmf5ALWppQE1XAAD0UHMB8Os/EEcVFwEVXAGY/kCNyp9dDQVg1kEr/wwC9FwzFwEVXAEAVKrwX2GLDkDh5w7gikqeY00EIO8DrwFq1MDkLPcKoORsAkxfsdMseQD8+g8wO6nnZ6FXAMUGE2AWypxphQYAgNTSBmB21y9lphKgG7ObbElfBXIFABCUAAAElTAAXv8BuFBprwK5AgAISgAAgiorAF7/AdqtqCmXKgA+AAzQK4kmallXAAA0pqAAFHVlBJBIObOuoAAA0CQBAAhKAACCEgCAoJIEwD2gAL2VYq6WcgVQztviAKkVMvFKCQAADRMAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACAoAQAISgAAghIAgKAEACCowdwbII9rht/17q98I/cuLu2pH/3L7h0/zL2LHtt871dv/8i/5t7Fpf3s39976tj+3LsgA1cAFGf43n/OvYWe6xve9uXce4CLCQDFuXrNLSs2bMm9i15aff37Fi/flHsXcDEBoEQt+315871fyb0FuAQBoERrb/nI/MWrcu+iNxYv37T6+vfl3gVcggBQov7+waF7Hsi9i94Y3vaVTqcv9y7gEgSAQm3acn9/f/V3qQ3OXbjxzvty7wIuTQAo1PzFq9a986O5d9Gt6+64b3De4ty7gEsTAMo1vK32+0H7hrd5+5dyCQDlWr7+zquvvTX3Lmbvms3vvmrlcO5dwGUJAEWr+gbKzdVfwdByAkDR1t/68bkLl+XexWwsWrZh9Q3vz70LeDsCQNH6B+Zuuvv+3LuYjeFtX+nr8/8XRfMfKKUb3vpAX99A7l3MzMCcBRvv/EzuXcAVCAClW7Dk2mtv+lDuXczMdXd8as78Jbl3AVcgAFSgundT3f1JFQSACqzcdO+Sa27MvYvpWjX0riWrbsi9C7gyAaAOFf1OXfWtq4QiANThuts/WcWr6guvXrfmxg/m3gVMiwBQh1ruqxne+mV3f1IL/6VSjeFtpc/WgcH5G+/6XO5dwHQV/b8TXGjRsusK/2aVDbd/cu6Cpbl3AdMlANSk8LeCW/ZNlrSeAFCT1de/d/GK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"


def get_enabled_strategy_names():
    try:
        with open(STRATEGIES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {s["name"] for s in data.get("strategies", []) if s.get("enabled", True)}
    except Exception:
        return None


def load_categories():
    """Returns the category -> [league names] dict. Empty dict if the
    file doesn't exist yet.
    """
    if not os.path.isfile(CATEGORIES_FILE):
        return {}
    with open(CATEGORIES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_categories(data):
    with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


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

    if s.get("flashscore_enabled"):
        step = s.get("flashscore_min_step")
        if not isinstance(step, int) or step < 1:
            return f"'{s['name']}': flashscore_min_step must be a positive whole number when Flashscore is enabled."

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
    error = validate_strategies(strategies)
    if error:
        raise ValueError(error)
    global_rules = load_global_risk_rules()
    _backup_current_file()
    with open(STRATEGIES_FILE, "w", encoding="utf-8") as f:
        json.dump({"global_risk_rules": global_rules, "strategies": strategies}, f, indent=2)


def save_global_risk_rules(rules):
    error = validate_global_risk_rules(rules)
    if error:
        raise ValueError(error)
    strategies = load_strategies_file()
    _backup_current_file()
    with open(STRATEGIES_FILE, "w", encoding="utf-8") as f:
        json.dump({"global_risk_rules": rules, "strategies": strategies}, f, indent=2)


def _connect_db():
    if not os.path.isfile(DB_PATH):
        return None
    return sqlite3.connect(DB_PATH)


def _load_leagues_seen():
    """config/leagues_seen.json — {sport: [league names]} collected from
    live markets by league_collector. Empty dict if missing/corrupt.
    """
    if not os.path.isfile(LEAGUES_SEEN_FILE):
        return {}
    try:
        with open(LEAGUES_SEEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, list)}


def get_league_sports():
    """['All'] plus every sport bucket league_collector has seen."""
    return ["All"] + sorted(_load_leagues_seen().keys())


def get_seen_leagues(sport="All"):
    """Every league the bot knows about, so categories can be built for
    leagues that haven't been bet on yet:

      - leagues_seen.json — all leagues collected from live markets,
        grouped by sport (the collector only keeps growing this);
      - bets.db — every distinct league that has actually shown up on a
        bet. Ground truth for real names, and it may hold leagues the
        collector hasn't scanned yet.

    sport="All" (default) returns the union; otherwise only the leagues
    in that sport bucket.
    """
    by_sport = _load_leagues_seen()
    leagues = {name for names in by_sport.values() for name in names}

    conn = _connect_db()
    if conn is not None:
        rows = conn.execute(
            "SELECT DISTINCT league FROM bets WHERE league IS NOT NULL AND league != ''"
        ).fetchall()
        conn.close()
        leagues.update(r[0] for r in rows)

    if sport and sport != "All":
        in_sport = set(by_sport.get(sport, []))
        leagues = {l for l in leagues if l in in_sport}

    return sorted(leagues, key=str.lower)


def get_open_bets():
    """Open bets read from the bot's per-strategy state files."""
    strategies = load_strategies_file()
    name_by_file = {_safe_filename(s["name"]): s["name"] for s in strategies if s.get("name")}

    open_bets = []
    if os.path.isdir(STATE_DIR):
        for fname in os.listdir(STATE_DIR):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(STATE_DIR, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                continue
            strategy_name = name_by_file.get(fname, fname[:-5])
            for bet in state.get("active_bets", []):
                open_bets.append({
                    "strategy_name": strategy_name,
                    "event_name": bet.get("event_name"),
                    "league": bet.get("league"),
                    "selection_name": bet.get("selection_name"),
                    "price": bet.get("price"),
                    "stake": bet.get("stake"),
                    "step": bet.get("step"),
                    "placed_at": bet.get("placed_at"),
                    "lock_at": bet.get("lock_at"),
                    "start_time": bet.get("start_time"),
                })
    open_bets.sort(key=lambda b: b.get("placed_at") or "", reverse=True)
    return open_bets


def restart_bot_container():
    try:
        client = docker.from_env()
        container = client.containers.get(BOT_CONTAINER)
        container.restart()
        return True, "Bot restarted."
    except docker.errors.NotFound:
        return False, f"Container '{BOT_CONTAINER}' not found."
    except Exception as e:
        return False, f"Could not restart bot: {e}"


def require_password(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not DASHBOARD_PASSWORD:
            return view(*args, **kwargs)
        if session.get("logged_in"):
            return view(*args, **kwargs)
        return redirect(url_for("login_page"))
    return wrapped


LOGIN_PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login</title>
</head>
<body style="background:#0a0e14;color:#eee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
<form method="POST" style="text-align:center;">
<input type="password" name="password" placeholder="Password" style="padding:10px;font-size:16px;background:#141a25;color:#eee;border:1px solid #333;border-radius:6px;">
<br><br>
<label style="font-size:13px;"><input type="checkbox" name="remember" checked> Remember me (30 days)</label>
<br><br>
<button type="submit" style="padding:10px 20px;background:#5b8def;color:#0a0e14;border:none;border-radius:6px;font-weight:600;">Login</button>
</form>
</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        if request.form.get("password") == DASHBOARD_PASSWORD:
            session.permanent = True
            session["logged_in"] = True
            return redirect(url_for("home"))
        return "Wrong password", 401
    return render_template_string(LOGIN_PAGE)


@app.route("/logout")
def logout_page():
    session.clear()
    return redirect(url_for("login_page"))


PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script>
document.write('<base href="' + (window.location.pathname.startsWith('/betdex') ? '/betdex/' : '/') + '">');
</script>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bet Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0a0e14">
<style>
  :root {
    --bg: #0a0e14;
    --card: #11161f;
    --card2: #141a25;
    --border: #1c2330;
    --border-thick: #2a3344;
    --text: #e4e7ec;
    --muted: #7a8699;
    --win: #2dd4a8;
    --loss: #ff6b5e;
    --pending: #f5b942;
    --accent: #5b8def;
  }
  * { box-sizing: border-box; }
  body {
    font-family: 'Inter', -apple-system, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 28px 32px 60px;
  }
  .topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; }
  .topbar { position: relative; flex-wrap: wrap; }
  .hamburger { display: none; background: none; border: 1px solid var(--border); color: var(--text); font-size: 18px; border-radius: 8px; padding: 6px 12px; cursor: pointer; }
  .nav-links { display: flex; gap: 10px; flex-wrap: wrap; }
  @media (max-width: 760px) {
    .hamburger { display: block; }
    .nav-links { display: none; position: absolute; top: 48px; right: 0; flex-direction: column; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px; z-index: 100; min-width: 200px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
    .nav-links.open { display: flex; }
    .nav-links .nav-btn { width: 100%; text-align: left; }
  }
  .topbar h0 {
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    color: var(--muted);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .nav-btn {
    font-family: 'Inter', sans-serif;
    font-size: 13px;
    font-weight: 600;
    color: var(--bg);
    background: var(--accent);
    border: none;
    border: none;
    border-radius: 8px;
    padding: 9px 16px;
    text-decoration: none;
    cursor: pointer;
  }
  .nav-btn.secondary { background: var(--card2); color: var(--text); border: 1px solid var(--border); }
  .nav-btn:hover { opacity: 0.85; }
  h1 {
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--muted);
    margin: 36px 0 12px;
  }
  h1:first-of-type { margin-top: 0; }
  table { border-collapse: collapse; width: 100%; }
  th, td {
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    font-size: 13.5px;
  }
  td { font-family: 'JetBrains Mono', monospace; }
  td:first-child, th:first-child { font-family: 'Inter', sans-serif; }
  th {
    color: var(--muted);
    font-weight: 500;
    font-size: 11.5px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 4px 16px; overflow-x: auto; }
  .profit { color: var(--win); }
  .loss { color: var(--loss); }
  .pending { color: var(--pending); }
  .strategy-block { border-top: 2px solid var(--border-thick); }
  .strategy-block:first-child { border-top: none; }
  .hero-row { display: flex; gap: 16px; margin-bottom: 8px; flex-wrap: wrap; }
  .hero-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 20px;
    flex: 1;
    min-width: 150px;
  }
  .hero-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
  .hero-value { font-family: 'JetBrains Mono', monospace; font-size: 24px; font-weight: 700; }
  .hero-sub { font-family: 'JetBrains Mono', monospace; font-size: 11.5px; color: var(--muted); margin-top: 6px; }
  .sub-tabs { display: flex; gap: 6px; margin-bottom: 10px; }
  .sub-tab {
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--muted);
    background: var(--card2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 12px;
    cursor: pointer;
  }
  .sub-tab.active { color: var(--text); border-color: var(--accent); }
  .period-row { display: none; }
  .period-row.active { display: block; }
  .saving-banner { background: rgba(245,185,66,0.12); border: 1px solid var(--pending); color: var(--pending); padding: 10px 14px; border-radius: 8px; font-family: 'JetBrains Mono', monospace; font-size: 13px; margin-bottom: 16px; }
  .small { color: var(--muted); font-size: 11.5px; font-family: 'JetBrains Mono', monospace; margin-bottom: 14px; }
  canvas { max-width: 100%; }
</style>
</head>
<body>
  <div class="topbar">
    <h0>betdex // live dashboard <span style="opacity:0.35;">{{ build }}</span></h0>
    <button class="hamburger" onclick="toggleNav()">☰</button>
    <div class="nav-links" id="navLinks">
      <a class="nav-btn secondary" href="strategies">⚙ Manage Strategies</a>
      <a class="nav-btn secondary" href="categories">Leagues</a>
      <a class="nav-btn secondary" href="analytics">Analytics</a>
      <a class="nav-btn secondary" href="logs">Logs</a>
      <a class="nav-btn secondary" href="league_skips">League Skips</a>
      <button class="nav-btn" style="background: var(--pending); color: #1a1300;" onclick="restartBot()">⟲ Restart Bot</button>
      <button class="nav-btn" onclick="location.reload()">⟳ Refresh</button>
    </div>
  </div>

  <div class="saving-banner" id="restartBanner" style="border-color: var(--accent); color: var(--accent); background: rgba(91,141,239,0.12); display:none;">Restarting the bot…</div>

  <div class="hero-row">
    <div class="hero-card">
      <div class="hero-label">BetDEX Balance</div>
      <div class="hero-value" id="balanceValue">—</div>
      <div class="hero-sub" id="balanceSub">loading…</div>
    </div>
  </div>

  <h1>Open Positions <span id="openCount" style="font-weight:400; text-transform:none; letter-spacing:0;">— currently pending</span></h1>
  <div class="card"><div id="pending"></div></div>

  <h1 style="display:flex; align-items:center; justify-content:space-between;">
    Strategy Performance
    <label style="font-family:'JetBrains Mono', monospace; font-size:11px; font-weight:400; text-transform:none; letter-spacing:0; color:var(--muted); display:flex; align-items:center; gap:6px; cursor:pointer;">
      <input type="checkbox" id="hideInactive" checked onchange="renderSummary()"> hide inactive
    </label>
  </h1>
  <div class="card"><div id="summary"></div></div>

  <h1>Profit</h1>
  <div class="card" style="padding: 16px;">
    <div class="sub-tabs">
      <div class="sub-tab active" data-period="daily">Daily (7d)</div>
      <div class="sub-tab" data-period="monthly">Monthly</div>
      <div class="sub-tab" data-period="yearly">Yearly</div>
    </div>
    <canvas id="profitChart" height="90"></canvas>
  </div>

  <h1 style="margin-top:26px;">Today's Bets</h1>
  <div class="card">
    <div style="display:flex; gap:10px; margin-bottom:12px; flex-wrap:wrap;">
      <select id="todayStrategyFilter" onchange="renderToday()" style="background:var(--card2); color:var(--text); border:1px solid var(--border); border-radius:6px; padding:6px 10px; font-family:'JetBrains Mono', monospace; font-size:12px;">
        <option value="">All strategies</option>
      </select>
    </div>
    <div id="today"></div>
  </div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/chartjs-plugin-datalabels/2.2.0/chartjs-plugin-datalabels.min.js"></script>
<script>
function toggleNav() { document.getElementById('navLinks').classList.toggle('open'); }
if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/service-worker.js').catch(() => {}); }

function fmt(n) { return (n >= 0 ? '+' : '') + n.toFixed(2); }
function cls(n) { return n >= 0 ? 'profit' : 'loss'; }

function loadBalance() {
  fetch('api/balance').then(r => r.json()).then(d => {
    const valEl = document.getElementById('balanceValue');
    const subEl = document.getElementById('balanceSub');
    if (!d.ok) {
      valEl.textContent = '—';
      subEl.textContent = d.error || 'unavailable';
      return;
    }
    const cur = d.currency || '';
    valEl.textContent = Number(d.balance).toFixed(2) + (cur ? ' ' + cur : '');
    const parts = ['available ' + Number(d.available).toFixed(2)];
    if (d.exposure) parts.push('exposure ' + Number(d.exposure).toFixed(2));
    subEl.textContent = parts.join(' · ');
  }).catch(() => {
    document.getElementById('balanceValue').textContent = '—';
    document.getElementById('balanceSub').textContent = 'unavailable';
  });
}
loadBalance();

function restartBot() {
  document.getElementById('restartBanner').style.display = 'block';
  fetch('api/restart_bot', { method: 'POST' })
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
if (window.ChartDataLabels) { Chart.register(window.ChartDataLabels); }

let _summaryData = [];

function renderSummary() {
  const hideInactive = document.getElementById('hideInactive').checked;
    let html = '<table><tr><th>Strategy</th><th>Bets</th><th>Won</th><th>Lost</th><th>Pending</th><th>Avg Odds</th><th>Implied Win %</th><th>Win %</th><th>Profit</th></tr>';
  _summaryData.forEach(s => {
    if (hideInactive && !s.enabled) return;
    const rowStyle = s.enabled ? '' : ' style="opacity:0.45;"';
    const nameLabel = s.enabled ? s.strategy_name : `${s.strategy_name} (inactive)`;
        html += `<tr${rowStyle}>
      <td>${nameLabel}</td>
      <td>${s.total}</td>
      <td>${s.won}</td>
      <td>${s.lost}</td>
      <td class="pending">${s.pending}</td>
      <td>${s.avg_odds}</td>
      <td>${s.implied_win_rate}%</td>
      <td>${s.win_rate}%</td>
      <td class="${cls(s.profit)}">${fmt(s.profit)}</td>
    </tr>`;
  });
  html += '</table>';
  document.getElementById('summary').innerHTML = html;
}

fetch('api/summary').then(r => r.json()).then(data => {
  _summaryData = data;
  renderSummary();
});

fetch('api/pending').then(r => r.json()).then(data => {
  document.getElementById('openCount').textContent = `(${data.length}) — currently pending`;
  if (!data.length) {
    document.getElementById('pending').innerHTML = '<p class="small">No open positions right now.</p>';
    return;
  }
  let html = '<table><tr><th>Placed</th><th>Strategy</th><th>League</th><th>Match</th><th>Selection</th><th>Odds</th><th>Stake</th><th>Step</th><th>Start</th></tr>';
  let lastStrategy = null;
  data.forEach(b => {
    const newBlock = lastStrategy !== null && lastStrategy !== b.strategy_name;
    html += `<tr class="${newBlock ? 'strategy-block' : ''}">
      <td>${(b.placed_at || '').replace('T', ' ').slice(0, 16)}</td>
      <td>${b.strategy_name}</td>
      <td>${b.league || '-'}</td>
      <td>${b.event_name}</td>
      <td>${b.selection_name}</td>
      <td>${b.odds}</td>
      <td>${b.stake}</td>
      <td>${b.step}</td>
      <td>${(b.start_time || '').replace('T', ' ').slice(0, 16) || '-'}</td>
    </tr>`;
    lastStrategy = b.strategy_name;
  });
  html += '</table>';
  document.getElementById('pending').innerHTML = html;
});

let _todayData = [];
function renderToday() {
  const filterVal = document.getElementById('todayStrategyFilter').value;
  const rows = filterVal ? _todayData.filter(b => b.strategy_name === filterVal) : _todayData;
  if (!rows.length) {
    document.getElementById('today').innerHTML = '<p class="small">No bets placed today.</p>';
    return;
  }
  let html = '<table><tr><th>Time</th><th>Strategy</th><th>League</th><th>Match</th><th>Selection</th><th>Odds</th><th>Stake</th><th>Step</th><th>Result</th><th>Profit</th></tr>';
  rows.forEach(b => {
    const resultClass = b.result === 'won' ? 'profit' : (b.result === 'lost' ? 'loss' : '');
    html += `<tr>
      <td>${(b.placed_at || '').replace('T', ' ').slice(0, 16)}</td>
      <td>${b.strategy_name}</td>
      <td>${b.league || '-'}</td>
      <td>${b.event_name}</td>
      <td>${b.selection_name}</td>
      <td>${b.odds}</td>
      <td>${b.stake}</td>
      <td>${b.step}</td>
      <td class="${resultClass}">${b.result || 'pending'}</td>
      <td class="${b.result ? cls(b.profit || 0) : ''}">${b.result ? fmt(b.profit || 0) : '-'}</td>
    </tr>`;
  });
  html += '</table>';
  document.getElementById('today').innerHTML = html;
}
fetch('api/today_bets').then(r => r.json()).then(data => {
  _todayData = data;
  const strategies = [...new Set(data.map(b => b.strategy_name))].sort();
  const sel = document.getElementById('todayStrategyFilter');
  strategies.forEach(s => sel.insertAdjacentHTML('beforeend', `<option value="${s}">${s}</option>`));
  renderToday();
});

let profitChart = null;
fetch('api/profit_periods').then(r => r.json()).then(data => {
  function draw(period) {
    const rows = data[period];
    const labels = rows.map(r => r.period);
    const values = rows.map(r => r.profit);
    const colors = values.map(v => v >= 0 ? '#2dd4a8' : '#ff6b5e');
    if (profitChart) profitChart.destroy();
    profitChart = new Chart(document.getElementById('profitChart'), {
      type: 'bar',
      data: { labels: labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 4 }] },
      options: {
        plugins: {
          legend: { display: false },
          datalabels: {
            color: '#e4e7ec',
            font: { family: 'JetBrains Mono', size: 11, weight: '600' },
            anchor: 'end',
            align: 'end',
            formatter: v => fmt(v)
          }
        },
        scales: {
          x: { grid: { color: '#1c2330' }, ticks: { color: '#7a8699', font: { family: 'JetBrains Mono', size: 11 } } },
          y: { grid: { color: '#1c2330' }, ticks: { color: '#7a8699', font: { family: 'JetBrains Mono', size: 11 } } }
        }
      }
    });
  }
  draw('daily');
  document.querySelectorAll('.sub-tab[data-period]').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.sub-tab[data-period]').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      draw(tab.dataset.period);
    });
  });
});
</script>
</body>
</html>
"""


ANALYTICS_PAGE = """
<!DOCTYPE html>
<html>
<head>
<script>
document.write('<base href="' + (window.location.pathname.startsWith('/betdex') ? '/betdex/' : '/') + '">');
</script>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0a0e14">
<style>
  :root {
    --bg: #0a0e14; --card: #11161f; --card2: #141a25;
    --border: #1c2330; --text: #e4e7ec; --muted: #7a8699;
    --win: #2dd4a8; --loss: #ff6b5e; --pending: #f5b942; --accent: #5b8def;
  }
  * { box-sizing: border-box; }
  body { font-family: 'Inter', -apple-system, Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 28px 32px 60px; }
  .topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; }
  .topbar { position: relative; flex-wrap: wrap; }
  .hamburger { display: none; background: none; border: 1px solid var(--border); color: var(--text); font-size: 18px; border-radius: 8px; padding: 6px 12px; cursor: pointer; }
  .nav-links { display: flex; gap: 10px; flex-wrap: wrap; }
  @media (max-width: 760px) {
    .hamburger { display: block; }
    .nav-links { display: none; position: absolute; top: 48px; right: 0; flex-direction: column; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px; z-index: 100; min-width: 200px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
    .nav-links.open { display: flex; }
    .nav-links .nav-btn { width: 100%; text-align: left; }
  }
  .topbar h0 { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--muted); letter-spacing: 0.12em; text-transform: uppercase; }
  .nav-btn { font-family: 'Inter', sans-serif; font-size: 13px; font-weight: 600; color: var(--bg); background: var(--accent); border: none; border-radius: 8px; padding: 9px 16px; text-decoration: none; cursor: pointer; }
  .nav-btn.secondary { background: var(--card2); color: var(--text); border: 1px solid var(--border); }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .full { grid-column: 1 / -1; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; }
  .card h2 { font-size: 13px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); margin: 0 0 14px; }
  canvas { max-width: 100%; }
  .empty { color: var(--muted); font-family: 'JetBrains Mono', monospace; font-size: 12px; padding: 30px 0; text-align: center; }
  .card-controls { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
  .sub-tabs { display: flex; gap: 4px; background: var(--card2); border: 1px solid var(--border); border-radius: 8px; padding: 3px; flex-wrap: wrap; }
  .sub-tab { font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--muted); padding: 5px 10px; border-radius: 6px; cursor: pointer; user-select: none; white-space: nowrap; }
  .sub-tab:hover { color: var(--text); }
  .sub-tab.active { background: var(--accent); color: #0a0e14; font-weight: 700; }
  select.control { background: var(--card2); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; font-family: 'JetBrains Mono', monospace; font-size: 11px; max-width: 260px; }
  .stat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .stat { background: var(--card2); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; min-width: 0; }
  .stat-label { font-family: 'JetBrains Mono', monospace; font-size: 10px; letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .stat-value { font-family: 'JetBrains Mono', monospace; font-size: 16px; font-weight: 700; color: var(--text); }
  .stat-sub { font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--muted); margin-top: 3px; }
  .pos { color: var(--win); }
  .neg { color: var(--loss); }
  .table-wrap { max-height: 360px; overflow-y: auto; margin-top: 18px; border: 1px solid var(--border); border-radius: 8px; }
  table.tbl { width: 100%; border-collapse: collapse; font-family: 'JetBrains Mono', monospace; font-size: 11px; }
  table.tbl th { position: sticky; top: 0; background: var(--card2); color: var(--muted); font-weight: 600; text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--border); cursor: pointer; white-space: nowrap; z-index: 1; }
  table.tbl th:first-child, table.tbl td:first-child { text-align: left; }
  table.tbl th:hover { color: var(--text); }
  table.tbl th.sorted { color: var(--accent); }
  table.tbl td { padding: 7px 10px; text-align: right; border-bottom: 1px solid var(--border); color: var(--text); white-space: nowrap; }
  table.tbl tbody tr:last-child td { border-bottom: none; }
  table.tbl tbody tr:hover { background: rgba(91, 141, 239, 0.06); }
  .note { color: var(--muted); font-family: 'JetBrains Mono', monospace; font-size: 11px; margin-top: 12px; line-height: 1.6; }
</style>
</head>
<body>
  <div class="topbar">
    <h0>betdex // analytics <span style="opacity:0.35;">{{ build }}</span></h0>
    <button class="hamburger" onclick="toggleNav()">☰</button>
    <div class="nav-links" id="navLinks">
      <a class="nav-btn secondary" href="">Dashboard</a>
      <a class="nav-btn secondary" href="strategies">⚙ Manage Strategies</a>
      <a class="nav-btn secondary" href="categories">Leagues</a>
      <a class="nav-btn secondary" href="logs">Logs</a>
      <a class="nav-btn secondary" href="league_skips">League Skips</a>
      <button class="nav-btn" onclick="location.reload()">⟳ Refresh</button>
    </div>
  </div>

  <div class="grid">
    <div class="card full">
      <h2>Performance by Day of Week <span style="font-weight:400; text-transform:none; letter-spacing:0;">— Mon to Sun, Europe/Athens</span></h2>
      <div class="card-controls">
        <div class="sub-tabs" id="weekdayMetricTabs">
          <div class="sub-tab active" data-metric="profit">Total Profit</div>
          <div class="sub-tab" data-metric="avg_profit">Avg / Day</div>
          <div class="sub-tab" data-metric="bets">Bets</div>
          <div class="sub-tab" data-metric="win_rate">Win Rate</div>
          <div class="sub-tab" data-metric="roi">ROI</div>
        </div>
        <select class="control" id="weekdayStrategyFilter"><option value="">All strategies</option></select>
      </div>
      <div class="stat-row" id="weekdayStats"></div>
      <div id="weekdayChartWrap">
        <canvas id="weekdayChart" height="90"></canvas>
        <div class="empty" id="weekdayChartEmpty" style="display:none;">No settled bets yet</div>
      </div>
      <div class="table-wrap">
        <table class="tbl" id="weekdayTable">
          <thead><tr>
            <th data-sort="weekday" data-label="Weekday">Weekday</th>
            <th data-sort="days" data-label="Days">Days</th>
            <th data-sort="bets" data-label="Bets">Bets</th>
            <th data-sort="won" data-label="Won">Won</th>
            <th data-sort="lost" data-label="Lost">Lost</th>
            <th data-sort="win_rate" data-label="Win %">Win %</th>
            <th data-sort="staked" data-label="Staked">Staked</th>
            <th data-sort="profit" data-label="Profit">Profit</th>
            <th data-sort="avg_profit" data-label="Avg / Day">Avg / Day</th>
            <th data-sort="roi" data-label="ROI %">ROI %</th>
            <th data-sort="avg_odds" data-label="Avg Odds">Avg Odds</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="note" id="weekdayNote"></div>
    </div>

    <div class="card full">
      <h2>Cumulative Profit Over Time</h2>
      <canvas id="cumulativeChart" height="70"></canvas>
    </div>

    <div class="card">
      <h2>Stake vs Profit</h2>
      <canvas id="scatterChart" height="160"></canvas>
    </div>

    <div class="card full">
      <h2>Profit per Strategy</h2>
      <canvas id="profitPerStrategyChart" height="90"></canvas>
    </div>

    <div class="card full">
      <h2>Profit per League <span style="font-weight:400; text-transform:none; letter-spacing:0;">— worst to best</span></h2>
      <canvas id="profitPerLeagueChart" height="120"></canvas>
    </div>
  </div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/chartjs-plugin-datalabels/2.2.0/chartjs-plugin-datalabels.min.js"></script>
<script>
function toggleNav() { document.getElementById('navLinks').classList.toggle('open'); }
if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/service-worker.js').catch(() => {}); }
if (window.ChartDataLabels) { Chart.register(window.ChartDataLabels); }
const muted = '#7a8699';
const gridColor = '#1c2330';
const win = '#2dd4a8';
const loss = '#ff6b5e';
const accent = '#5b8def';
const palette = ['#5b8def', '#2dd4a8', '#f5b942', '#ff6b5e', '#a78bfa', '#38bdf8'];

const baseTicks = { color: muted, font: { family: 'JetBrains Mono', size: 11 } };
const baseGrid = { color: gridColor };

fetch('api/chart_data').then(r => r.json()).then(data => {

  if (data.cumulative.length) {
    let running = 0;
    const labels = [];
    const values = [];
    data.cumulative.forEach(b => {
      running += b.profit;
      labels.push((b.settled_at || '').slice(0, 16).replace('T', ' '));
      values.push(running);
    });
    new Chart(document.getElementById('cumulativeChart'), {
      type: 'line',
      data: { labels: labels, datasets: [{
        data: values, borderColor: accent, backgroundColor: 'rgba(91,141,239,0.08)',
        fill: true, tension: 0.25, pointRadius: 0, borderWidth: 2
      }]},
      options: {
        plugins: { legend: { display: false }, datalabels: { display: false } },
        scales: {
          x: { grid: baseGrid, ticks: { ...baseTicks, maxTicksLimit: 8, maxRotation: 0 } },
          y: { grid: baseGrid, ticks: baseTicks }
        }
      }
    });
  } else {
    document.getElementById('cumulativeChart').outerHTML = '<div class="empty">No settled bets yet</div>';
  }

  if (data.stake_vs_profit.length) {
    const points = data.stake_vs_profit.map(b => ({ x: b.stake, y: b.profit }));
    const colors = data.stake_vs_profit.map(b => b.result === 'won' ? win : loss);
    new Chart(document.getElementById('scatterChart'), {
      type: 'scatter',
      data: { datasets: [{ data: points, backgroundColor: colors, pointRadius: 5 }] },
      options: {
        plugins: { legend: { display: false }, datalabels: { display: false } },
        scales: {
          x: { title: { display: true, text: 'Stake', color: muted }, grid: baseGrid, ticks: baseTicks },
          y: { title: { display: true, text: 'Profit', color: muted }, grid: baseGrid, ticks: baseTicks }
        }
      }
    });
  } else {
    document.getElementById('scatterChart').outerHTML = '<div class="empty">No settled bets yet</div>';
  }

  if (data.profit_per_strategy && data.profit_per_strategy.length) {
    const labels = data.profit_per_strategy.map(s => s.strategy_name);
    const values = data.profit_per_strategy.map(s => s.profit);
    const colors = values.map(v => v >= 0 ? win : loss);
    new Chart(document.getElementById('profitPerStrategyChart'), {
      type: 'bar',
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 4 }] },
      options: {
        indexAxis: 'y',
        plugins: {
          legend: { display: false },
          datalabels: {
            color: '#e4e7ec',
            font: { family: 'JetBrains Mono', size: 11, weight: '600' },
            anchor: 'end',
            align: 'right',
            formatter: v => (v >= 0 ? '+' : '') + v.toFixed(2)
          }
        },
        scales: { x: { grid: baseGrid, ticks: baseTicks }, y: { grid: baseGrid, ticks: baseTicks } }
      }
    });
  } else if (document.getElementById('profitPerStrategyChart')) {
    document.getElementById('profitPerStrategyChart').outerHTML = '<div class="empty">No settled bets yet</div>';
  }

  if (data.profit_per_league && data.profit_per_league.length) {
    const labels = data.profit_per_league.map(l => l.league);
    const values = data.profit_per_league.map(l => l.profit);
    const winRates = data.profit_per_league.map(l => l.win_rate);
    const colors = values.map(v => v >= 0 ? win : loss);
    new Chart(document.getElementById('profitPerLeagueChart'), {
      type: 'bar',
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 4 }] },
      options: {
        indexAxis: 'y',
        plugins: {
          legend: { display: false },
          datalabels: {
            color: '#e4e7ec',
            font: { family: 'JetBrains Mono', size: 11, weight: '600' },
            anchor: 'end',
            align: 'right',
            formatter: v => (v >= 0 ? '+' : '') + v.toFixed(2)
          },
          tooltip: {
            callbacks: {
              afterLabel: (ctx) => `Win rate: ${winRates[ctx.dataIndex]}%`
            }
          }
        },
        scales: { x: { grid: baseGrid, ticks: baseTicks }, y: { grid: baseGrid, ticks: baseTicks } }
      }
    });
  } else if (document.getElementById('profitPerLeagueChart')) {
    document.getElementById('profitPerLeagueChart').outerHTML = '<div class="empty">No league data yet — leagues are captured as new bets are placed</div>';
  }
});

// ---------------------------------------------------------------------------
// Performance by Day of Week report
// ---------------------------------------------------------------------------
const weekdayMetrics = {
  profit:     { label: 'Profit',    get: w => w.profit,     signed: true,  color: w => w.profit >= 0 ? win : loss },
  avg_profit: { label: 'Avg / day', get: w => w.avg_profit, signed: true,  color: w => w.avg_profit >= 0 ? win : loss },
  bets:       { label: 'Bets',      get: w => w.bets,       signed: false, color: () => accent },
  win_rate:   { label: 'Win rate',  get: w => w.win_rate,   signed: false, color: () => accent },
  roi:        { label: 'ROI',       get: w => w.roi,        signed: true,  color: w => w.roi >= 0 ? win : loss }
};

let weekdayChart = null;
let weekdayData = { weekdays: [], summary: {} };
let weekdaySort = { key: 'weekday', dir: 'asc' };

function signed(n, dp) {
  const d = dp === undefined ? 2 : dp;
  return (Number(n) >= 0 ? '+' : '') + Number(n).toFixed(d);
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderWeekdayStats(s) {
  const d = Object.assign({
    profit: 0, staked: 0, bets: 0, won: 0, lost: 0,
    win_rate: 0, roi: 0, avg_profit: 0, trading_days: 0
  }, s || {});
  const best = d.best_day, worst = d.worst_day, busy = d.busiest;
  const tiles = [
    { label: 'Best weekday', value: best ? best.name : '—', cls: 'pos', sub: best ? signed(best.avg_profit, 2) + ' / day' : '' },
    { label: 'Worst weekday', value: worst ? worst.name : '—', cls: 'neg', sub: worst ? signed(worst.avg_profit, 2) + ' / day' : '' },
    { label: 'Busiest', value: busy ? busy.name : '—', sub: busy ? busy.bets + ' bets' : '' },
    { label: 'Net profit', value: signed(d.profit, 2), cls: d.profit >= 0 ? 'pos' : 'neg', sub: d.trading_days + ' trading days' },
    { label: 'Win rate', value: Number(d.win_rate).toFixed(1) + '%', sub: d.won + 'W / ' + d.lost + 'L' },
    { label: 'ROI', value: signed(d.roi, 1) + '%', cls: d.roi >= 0 ? 'pos' : 'neg' },
    { label: 'Staked', value: Number(d.staked).toFixed(2), sub: d.bets + ' bets' },
    { label: 'Avg / trading day', value: signed(d.avg_profit, 2), cls: d.avg_profit >= 0 ? 'pos' : 'neg' }
  ];
  document.getElementById('weekdayStats').innerHTML = tiles.map(t =>
    '<div class="stat">' +
      '<div class="stat-label">' + t.label + '</div>' +
      '<div class="stat-value ' + (t.cls || '') + '">' + t.value + '</div>' +
      (t.sub ? '<div class="stat-sub">' + t.sub + '</div>' : '') +
    '</div>'
  ).join('');
}

function drawWeekdayChart() {
  const canvas = document.getElementById('weekdayChart');
  const empty = document.getElementById('weekdayChartEmpty');
  if (!canvas) return;
  const rows = weekdayData.weekdays || [];
  if (!rows.length || !rows.some(r => r.bets > 0)) {
    if (weekdayChart) { weekdayChart.destroy(); weekdayChart = null; }
    canvas.style.display = 'none';
    if (empty) empty.style.display = 'block';
    return;
  }
  canvas.style.display = '';
  if (empty) empty.style.display = 'none';

  const activeTab = document.querySelector('#weekdayMetricTabs .sub-tab.active');
  const metric = activeTab ? activeTab.dataset.metric : 'profit';
  const cfg = weekdayMetrics[metric] || weekdayMetrics.profit;
  const values = rows.map(cfg.get);
  const colors = rows.map(cfg.color);

  if (weekdayChart) { weekdayChart.destroy(); weekdayChart = null; }
  weekdayChart = new Chart(canvas, {
    type: 'bar',
    data: { labels: rows.map(w => w.name), datasets: [{ data: values, backgroundColor: colors, borderRadius: 4 }] },
    options: {
      plugins: {
        legend: { display: false },
        datalabels: {
          color: '#e4e7ec',
          font: { family: 'JetBrains Mono', size: 11, weight: '600' },
          anchor: 'end',
          align: 'end',
          formatter: v => cfg.signed ? signed(v, 2) : (metric === 'bets' ? String(v) : Number(v).toFixed(1))
        },
        tooltip: {
          callbacks: {
            label: item => {
              const w = rows[item.dataIndex];
              return [
                'Profit: ' + signed(w.profit, 2) + '  (' + signed(w.avg_profit, 2) + ' / day)',
                'Bets: ' + w.bets + '  (' + w.won + 'W / ' + w.lost + 'L)',
                'Win rate: ' + Number(w.win_rate).toFixed(1) + '%',
                'ROI: ' + signed(w.roi, 1) + '%',
                'Trading days: ' + w.days
              ];
            }
          }
        }
      },
      scales: {
        x: { grid: baseGrid, ticks: { ...baseTicks, maxRotation: 0 } },
        y: { grid: baseGrid, ticks: baseTicks }
      }
    }
  });
}

function renderWeekdayTable() {
  const tbody = document.querySelector('#weekdayTable tbody');
  if (!tbody) return;
  const rows = (weekdayData.weekdays || []).slice();
  const key = weekdaySort.key;
  const dir = weekdaySort.dir === 'asc' ? 1 : -1;
  rows.sort((a, b) => {
    const av = a[key], bv = b[key];
    if (typeof av === 'string' || typeof bv === 'string') {
      // plain comparison, not localeCompare — ISO dates must sort as strings
      return dir * (av < bv ? -1 : (av > bv ? 1 : 0));
    }
    return dir * (av - bv);
  });
  tbody.innerHTML = rows.map(w =>
    '<tr>' +
      '<td>' + w.name + '</td>' +
      '<td>' + w.days + '</td>' +
      '<td>' + w.bets + '</td>' +
      '<td>' + w.won + '</td>' +
      '<td>' + w.lost + '</td>' +
      '<td>' + Number(w.win_rate).toFixed(1) + '%</td>' +
      '<td>' + Number(w.staked).toFixed(2) + '</td>' +
      '<td class="' + (w.profit >= 0 ? 'pos' : 'neg') + '">' + signed(w.profit, 2) + '</td>' +
      '<td class="' + (w.avg_profit >= 0 ? 'pos' : 'neg') + '">' + signed(w.avg_profit, 2) + '</td>' +
      '<td class="' + (w.roi >= 0 ? 'pos' : 'neg') + '">' + signed(w.roi, 1) + '%</td>' +
      '<td>' + Number(w.avg_odds).toFixed(2) + '</td>' +
    '</tr>'
  ).join('');

  document.querySelectorAll('#weekdayTable th[data-sort]').forEach(th => {
    const on = th.dataset.sort === key;
    th.classList.toggle('sorted', on);
    const base = th.dataset.label || th.dataset.sort;
    th.textContent = base + (on ? (weekdaySort.dir === 'asc' ? ' ▲' : ' ▼') : '');
  });
}

function fillWeekdayStrategies(list) {
  const sel = document.getElementById('weekdayStrategyFilter');
  if (!sel || !list || !list.length) return;
  const current = sel.value;
  if (sel.dataset.filled === '1' && sel.options.length === list.length + 1) return;
  sel.innerHTML = '<option value="">All strategies</option>' +
    list.map(s => '<option value="' + escHtml(s) + '">' + escHtml(s) + '</option>').join('');
  sel.value = current;
  sel.dataset.filled = '1';
}

function loadWeekday() {
  const sel = document.getElementById('weekdayStrategyFilter');
  const strategy = sel ? sel.value : '';
  const note = document.getElementById('weekdayNote');
  if (note) note.textContent = 'Loading…';

  fetch('api/weekday_performance?strategy=' + encodeURIComponent(strategy))
    .then(r => r.json())
    .then(data => {
      weekdayData = data || { weekdays: [] };
      fillWeekdayStrategies(data.strategies);
      renderWeekdayStats(data.summary || {});
      drawWeekdayChart();
      renderWeekdayTable();
      if (note) {
        note.textContent =
          'A weekday bucket spans many weeks, so totals alone favour the weekdays that happened to occur most often.'
          + ' Avg / Day divides by the trading days in the Days column.'
          + ' Days are Europe/Athens calendar days in which bets settled.'
          + (strategy ? ' Filtered to "' + strategy + '".' : '')
          + ' Click a column header to sort.';
      }
    })
    .catch(e => {
      if (note) note.textContent = 'Could not load weekday performance: ' + e;
    });
}

document.querySelectorAll('#weekdayMetricTabs .sub-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('#weekdayMetricTabs .sub-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    drawWeekdayChart();
  });
});

document.querySelectorAll('#weekdayTable th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const key = th.dataset.sort;
    if (weekdaySort.key === key) {
      weekdaySort.dir = weekdaySort.dir === 'asc' ? 'desc' : 'asc';
    } else {
      weekdaySort.key = key;
      weekdaySort.dir = 'asc';
    }
    renderWeekdayTable();
  });
});

const weekdaySelect = document.getElementById('weekdayStrategyFilter');
if (weekdaySelect) weekdaySelect.addEventListener('change', loadWeekday);

loadWeekday();
</script>
</body>
</html>
"""


CATEGORIES_PAGE = """
<!DOCTYPE html>
<html>
<head>
<script>
document.write('<base href="' + (window.location.pathname.startsWith('/betdex') ? '/betdex/' : '/') + '">');
</script>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>League Categories</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0a0e14">
<style>
  :root {
    --bg: #0a0e14; --card: #11161f; --card2: #141a25;
    --border: #1c2330; --text: #e4e7ec; --muted: #7a8699; --accent: #5b8def; --loss: #ff6b5e; --win: #2dd4a8;
  }
  * { box-sizing: border-box; }
  body { font-family: 'Inter', -apple-system, Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 28px 32px 60px; }
  .topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; position: relative; flex-wrap: wrap; }
  .hamburger { display: none; background: none; border: 1px solid var(--border); color: var(--text); font-size: 18px; border-radius: 8px; padding: 6px 12px; cursor: pointer; }
  .nav-links { display: flex; gap: 10px; flex-wrap: wrap; }
  @media (max-width: 760px) {
    .hamburger { display: block; }
    .nav-links { display: none; position: absolute; top: 48px; right: 0; flex-direction: column; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px; z-index: 100; min-width: 200px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
    .nav-links.open { display: flex; }
    .nav-links .nav-btn { width: 100%; text-align: left; }
  }
  .topbar h0 { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--muted); letter-spacing: 0.12em; text-transform: uppercase; }
  .nav-btn { font-family: 'Inter', sans-serif; font-size: 13px; font-weight: 600; color: var(--bg); background: var(--accent); border: none; border-radius: 8px; padding: 9px 16px; text-decoration: none; cursor: pointer; }
  .nav-btn.secondary { background: var(--card2); color: var(--text); border: 1px solid var(--border); }
  .layout { display: grid; grid-template-columns: 1fr 1.3fr; gap: 18px; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
  .card h2 { font-size: 13px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); margin: 0 0 14px; }
  .scroll-panel { max-height: 68vh; overflow-y: auto; padding-right: 4px; }
  .league-item { display: flex; align-items: center; gap: 8px; padding: 6px 4px; font-family: 'JetBrains Mono', monospace; font-size: 12.5px; border-bottom: 1px solid var(--border); }
  .cat-block { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; background: var(--card2); }
  .cat-title-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; gap: 8px; cursor: pointer; }
  .cat-title { font-weight: 600; font-size: 14px; }
  .cat-toggle { color: var(--muted); font-size: 12px; width: 14px; flex-shrink: 0; }
  .cat-actions { display: flex; gap: 6px; flex-shrink: 0; }
  .cat-league-row { display: flex; align-items: center; gap: 6px; font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--muted); padding: 2px 0; }
  .cat-league-row a { color: var(--loss); text-decoration: none; margin-left: auto; }
  .cat-bulk-remove { font-size: 11px; margin-top: 6px; }
  .btn { font-family: 'JetBrains Mono', monospace; font-size: 12px; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--card2); color: var(--text); cursor: pointer; }
  .btn.small { padding: 4px 8px; font-size: 11px; }
  .btn.danger:hover { border-color: var(--loss); color: var(--loss); }
  .btn.accent { border-color: var(--accent); color: var(--accent); }
  input[type=text] { background: var(--card2); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 8px 10px; font-family: 'JetBrains Mono', monospace; font-size: 13px; width: 100%; }
  .add-cat-row { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  select { background: var(--card2); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 6px 8px; font-family: 'JetBrains Mono', monospace; font-size: 12px; }
  .bulk-row { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
  .checkbox-row { display: flex; align-items: center; gap: 6px; flex: 1; }
</style>
</head>
<body>
  <div class="topbar">
    <h0>betdex // league categories <span style="opacity:0.35;">{{ build }}</span></h0>
    <button class="hamburger" onclick="toggleNav()">☰</button>
    <div class="nav-links" id="navLinks">
      <a class="nav-btn secondary" href="">Dashboard</a>
      <a class="nav-btn secondary" href="strategies">⚙ Manage Strategies</a>
      <a class="nav-btn secondary" href="analytics">Analytics</a>
      <a class="nav-btn secondary" href="logs">Logs</a>
      <a class="nav-btn secondary" href="league_skips">League Skips</a>
      <button class="nav-btn" onclick="location.reload()">⟳ Refresh</button>
    </div>
  </div>

  <div class="add-cat-row">
    <select id="sportSelector" onchange="switchSport()" style="background:var(--card2); color:var(--text); border:1px solid var(--border); border-radius:6px; padding:8px 12px; font-family:'Inter', sans-serif; font-size:13px;"></select>
    <input type="text" id="newCatName" placeholder="New category name (e.g. Top Leagues)">
    <button class="btn" onclick="addCategory()">+ Add category</button>
  </div>

  <div class="layout">
    <div class="card">
      <h2 id="leagueListTitle">All Leagues</h2>
      <div class="bulk-row">
        <select id="bulkCatSelect"></select>
        <button class="btn accent" onclick="bulkAdd()">Add checked leagues to category</button>
        <button class="btn small" onclick="selectAllLeagues()">Select all</button>
        <button class="btn small" onclick="clearAllLeagues()">Clear</button>
      </div>
      <div id="leagueFilterRow" style="display:none;">
        <input type="text" id="leagueFilter" placeholder="Filter leagues (checked ones stay visible)…" oninput="filterLeagueList(this.value)" style="margin-bottom:10px;">
      </div>
      <div id="leagueNoMatch" style="display:none; color:var(--muted); font-size:12px;">No leagues match that filter.</div>
      <div id="leagueList" class="scroll-panel"></div>
    </div>
    <div class="card">
      <h2>Categories</h2>
      <div id="catList" class="scroll-panel"></div>
    </div>
  </div>

<script>
function toggleNav() { document.getElementById('navLinks').classList.toggle('open'); }
if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/service-worker.js').catch(() => {}); }
let leagues = [];
let categories = {};
let currentSport = 'All';
let collapsedCats = new Set();

// League/category names come from the API and from user input, so they
// get escaped before going into innerHTML.
function escHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function setLeagueListTitle() {
  document.getElementById('leagueListTitle').textContent =
    currentSport === 'All' ? 'All Leagues' : `All Leagues — ${currentSport}`;
}

function toggleCatCollapse(cat, safeCat) {
  if (collapsedCats.has(cat)) {
    collapsedCats.delete(cat);
  } else {
    collapsedCats.add(cat);
  }
  const body = document.getElementById('catbody_' + safeCat);
  if (body) body.style.display = collapsedCats.has(cat) ? 'none' : '';
  const arrow = document.querySelector(`#catbody_${safeCat}`)?.previousElementSibling?.querySelector('.cat-toggle');
  if (arrow) arrow.textContent = collapsedCats.has(cat) ? '▸' : '▾';
}

function switchSport() {
  currentSport = document.getElementById('sportSelector').value;
  setLeagueListTitle();
  loadLeaguesForSport();
}

function loadLeaguesForSport() {
  fetch('api/leagues?sport=' + encodeURIComponent(currentSport)).then(r => r.json()).then(l => {
    leagues = (l || []).slice().sort((a, b) => a.localeCompare(b));
    const f = document.getElementById('leagueFilter');
    if (f) f.value = '';
    render();
  });
}

// Checked leagues stay visible regardless of the filter, so a filter
// can't hide a selection from bulkAdd.
function filterLeagueList(term) {
  const t = (term || '').trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll('#leagueList .league-item').forEach(row => {
    const cb = row.querySelector('.league-check');
    const match = !t || !cb || cb.value.toLowerCase().includes(t) || (cb && cb.checked);
    row.style.display = match ? '' : 'none';
    if (match) shown++;
  });
  document.getElementById('leagueNoMatch').style.display = shown ? 'none' : '';
}

function load(keepSport) {
  Promise.all([
    fetch('api/leagues_sports').then(r => r.json()),
    fetch('api/league_categories').then(r => r.json())
  ]).then(([sports, c]) => {
    const list = sports.length ? sports : ['Soccer'];
    const sel = document.getElementById('sportSelector');
    sel.innerHTML = list.map(s => `<option value="${s}">${s}</option>`).join('');
    if (keepSport && list.includes(currentSport)) {
      sel.value = currentSport;
    } else {
      currentSport = sel.value || 'All';
    }
    setLeagueListTitle();

    categories = c || {};
    Object.keys(categories).forEach(cat => {
      categories[cat] = (categories[cat] || []).slice().sort((a, b) => a.localeCompare(b));
    });
    loadLeaguesForSport();
  });
}

function render() {
  const catNames = Object.keys(categories).sort();

  const bulkSel = document.getElementById('bulkCatSelect');
  bulkSel.innerHTML = catNames.length
    ? catNames.map(c => `<option value="${c}">${c}</option>`).join('')
    : '<option value="">-- add a category first --</option>';

  let leagueHtml = '';
  leagues.forEach(name => {
    leagueHtml += `<div class="league-item">
      <input type="checkbox" class="league-check" value="${escAttr(name)}">
      <span style="flex:1;">${escHtml(name)}</span>
    </div>`;
  });
  document.getElementById('leagueList').innerHTML = leagueHtml || '<p style="color:var(--muted);">No leagues known yet — the collector picks them up as markets open.</p>';
  document.getElementById('leagueFilterRow').style.display = leagues.length ? '' : 'none';

  let catHtml = '';
  catNames.forEach(cat => {
    const inCat = categories[cat] || [];
    const safeCat = safeId(cat);
    const isCollapsed = collapsedCats.has(cat);
    catHtml += `<div class="cat-block">
      <div class="cat-title-row" onclick="toggleCatCollapse('${escName(cat)}','${safeCat}')">
        <span class="cat-toggle">${isCollapsed ? '▸' : '▾'}</span>
        <div class="cat-title" style="flex:1;">${escHtml(cat)} (${inCat.length})</div>
        <div class="cat-actions" onclick="event.stopPropagation()">
          <button class="btn small" onclick="copyCategory('${escName(cat)}')">Copy</button>
          <button class="btn small danger" onclick="deleteCategory('${escName(cat)}')">Delete</button>
        </div>
      </div>
      <div id="catbody_${safeCat}" style="display:${isCollapsed ? 'none' : ''};">
        ${inCat.map(l => `<div class="cat-league-row">
            <input type="checkbox" class="rm_${safeCat}" value="${escAttr(l)}">
            <span>${escHtml(l)}</span>
          </div>`).join('') || '<span style="color:var(--muted); font-size:12px;">(empty)</span>'}
        ${inCat.length ? `<button class="btn small danger cat-bulk-remove" onclick="bulkRemove('${escName(cat)}','${safeCat}')">Remove checked</button>` : ''}
      </div>
    </div>`;
  });
  document.getElementById('catList').innerHTML = catHtml || '<p style="color:var(--muted);">No categories yet — add one above.</p>';
}

function safeId(name) { return name.replace(/[^a-zA-Z0-9]/g, '_'); }
function escName(name) { return name.replace(/'/g, "\\\\'"); }
function escAttr(name) { return name.replace(/"/g, '&quot;'); }

function selectAllLeagues() {
  document.querySelectorAll('.league-check').forEach(cb => cb.checked = true);
}
function clearAllLeagues() {
  document.querySelectorAll('.league-check').forEach(cb => cb.checked = false);
}

function addCategory() {
  const name = document.getElementById('newCatName').value.trim();
  if (!name) return;
  if (!categories[name]) categories[name] = [];
  document.getElementById('newCatName').value = '';
  persist();
}

function deleteCategory(name) {
  if (!confirm('Delete category "' + name + '"? Strategies using it will stop filtering by it.')) return;
  delete categories[name];
  persist();
}

function copyCategory(name) {
  const newName = prompt('Copy "' + name + '" as:', name + ' Copy');
  if (!newName || !newName.trim()) return;
  categories[newName.trim()] = (categories[name] || []).slice();
  persist();
}

function bulkAdd() {
  const cat = document.getElementById('bulkCatSelect').value;
  if (!cat) { alert('Add a category first.'); return; }
  const checked = Array.from(document.querySelectorAll('.league-check:checked')).map(cb => cb.value);
  if (!checked.length) return;
  if (!categories[cat]) categories[cat] = [];
  checked.forEach(l => { if (!categories[cat].includes(l)) categories[cat].push(l); });
  persist();
}

function bulkRemove(cat, safeCat) {
  const checked = Array.from(document.querySelectorAll('.rm_' + safeCat + ':checked')).map(cb => cb.value);
  if (!checked.length) return;
  categories[cat] = (categories[cat] || []).filter(l => !checked.includes(l));
  persist();
}

function persist() {
  fetch('api/league_categories', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(categories)
  }).then(r => r.json()).then(() => load(true));
}

load(false);
</script>
</body>
</html>
"""
STRATEGIES_PAGE = """
<!DOCTYPE html>
<html>
<head>
<script>
document.write('<base href="' + (window.location.pathname.startsWith('/betdex') ? '/betdex/' : '/') + '">');
</script>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Manage Strategies</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0a0e14">
<style>
  :root {
    --bg: #0a0e14; --card: #11161f; --card2: #141a25;
    --border: #1c2330; --text: #e4e7ec; --muted: #7a8699;
    --win: #2dd4a8; --loss: #ff6b5e; --pending: #f5b942; --accent: #5b8def;
  }
  * { box-sizing: border-box; }
  body { font-family: 'Inter', -apple-system, Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 28px 32px 60px; }
  .topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; }
  .topbar { position: relative; flex-wrap: wrap; }
  .hamburger { display: none; background: none; border: 1px solid var(--border); color: var(--text); font-size: 18px; border-radius: 8px; padding: 6px 12px; cursor: pointer; }
  .nav-links { display: flex; gap: 10px; flex-wrap: wrap; }
  @media (max-width: 760px) {
    .hamburger { display: block; }
    .nav-links { display: none; position: absolute; top: 48px; right: 0; flex-direction: column; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px; z-index: 100; min-width: 200px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
    .nav-links.open { display: flex; }
    .nav-links .nav-btn { width: 100%; text-align: left; }
  }
  .topbar h0 { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--muted); letter-spacing: 0.12em; text-transform: uppercase; }
  .nav-btn { font-family: 'Inter', sans-serif; font-size: 13px; font-weight: 600; color: var(--bg); background: var(--accent); border: none; border-radius: 8px; padding: 9px 16px; text-decoration: none; cursor: pointer; }
  .nav-btn.secondary { background: var(--card2); color: var(--text); border: 1px solid var(--border); }
  h1 { font-size: 14px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); margin: 30px 0 12px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; }
  .strat-row { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
  .strat-name { font-weight: 600; font-size: 14.5px; }
  .strat-meta { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--muted); margin-top: 4px; }
  .badge { font-family: 'JetBrains Mono', monospace; font-size: 11px; padding: 3px 8px; border-radius: 4px; margin-right: 8px; }
  .badge.on { background: rgba(45,212,168,0.15); color: var(--win); }
  .badge.off { background: rgba(122,134,153,0.15); color: var(--muted); }
  .row-actions { display: flex; gap: 8px; flex-wrap: wrap; }
  .btn { font-family: 'JetBrains Mono', monospace; font-size: 12px; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--card2); color: var(--text); cursor: pointer; }
  .btn:hover { border-color: var(--accent); }
  .btn.danger:hover { border-color: var(--loss); color: var(--loss); }
  .btn.reset:hover { border-color: var(--pending); color: var(--pending); }
  .btn.accent { border-color: var(--accent); color: var(--accent); }
  .add-btn { font-family: 'JetBrains Mono', monospace; font-size: 13px; padding: 10px 16px; border-radius: 8px; border: 1px dashed var(--border); background: transparent; color: var(--muted); cursor: pointer; width: 100%; margin-top: 6px; }
  .add-btn:hover { border-color: var(--accent); color: var(--accent); }

  .risk-card { display: flex; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
  .risk-field label { display: block; font-size: 11.5px; color: var(--muted); margin-bottom: 5px; font-family: 'JetBrains Mono', monospace; }
  .risk-field input { width: 170px; background: var(--card2); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 8px 10px; font-family: 'JetBrains Mono', monospace; font-size: 13px; }
  .risk-saved { color: var(--win); font-family: 'JetBrains Mono', monospace; font-size: 12px; display: none; }
  .empty-note { color: var(--muted); font-family: 'JetBrains Mono', monospace; font-size: 13px; }

  .modal-bg { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6); align-items: center; justify-content: center; z-index: 10; }
  .modal-bg.open { display: flex; }
  .modal { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; width: 640px; max-height: 85vh; overflow-y: auto; }
  .modal h2 { margin: 0 0 16px; font-size: 15px; }
  .field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .field { margin-bottom: 12px; }
  .field.full { grid-column: 1 / -1; }
  .field label { display: block; font-size: 11.5px; color: var(--muted); margin-bottom: 5px; font-family: 'JetBrains Mono', monospace; }
  .field input, .field select { width: 100%; background: var(--card2); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 8px 10px; font-family: 'JetBrains Mono', monospace; font-size: 13px; }
  .field input[type="checkbox"] { width: auto; }
  .checkbox-row { display: flex; align-items: center; gap: 8px; }
  .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
  .error-box { background: rgba(255,107,94,0.12); border: 1px solid var(--loss); color: var(--loss); padding: 10px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 14px; display: none; }
  .saving-banner { display: none; background: rgba(245,185,66,0.12); border: 1px solid var(--pending); color: var(--pending); padding: 10px 14px; border-radius: 8px; font-family: 'JetBrains Mono', monospace; font-size: 13px; margin-bottom: 16px; }
  .market-row { background: var(--card2); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; position: relative; }
  .subtabs { display: flex; gap: 6px; margin-bottom: 10px; }
  .subtab-btn { font-family: 'JetBrains Mono', monospace; font-size: 11.5px; color: var(--muted); background: var(--card2); border: 1px solid var(--border); border-radius: 6px; padding: 5px 10px; cursor: pointer; }
  .subtab-btn.active { color: var(--text); border-color: var(--accent); }
  .subtab-pane { display: none; max-height: 180px; overflow-y: auto; background: var(--card2); border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; }
  .subtab-pane.active { display: block; }
</style>
</head>
<body>
  <div class="topbar">
    <h0>betdex // manage strategies <span style="opacity:0.35;">{{ build }}</span></h0>
    <button class="hamburger" onclick="toggleNav()">☰</button>
    <div class="nav-links" id="navLinks">
      <a class="nav-btn secondary" href="">Dashboard</a>
      <a class="nav-btn secondary" href="categories">Leagues</a>
      <a class="nav-btn secondary" href="analytics">Analytics</a>
      <a class="nav-btn secondary" href="logs">Logs</a>
      <a class="nav-btn secondary" href="league_skips">League Skips</a>
      <button class="nav-btn" style="background: var(--pending); color: #1a1300;" onclick="restartBot()">⟲ Restart Bot</button>
      <button class="nav-btn" onclick="location.reload()">⟳ Refresh</button>
    </div>
  </div>

  <div class="saving-banner" id="savingBanner">Saving…</div>
  <div class="saving-banner" id="restartBanner" style="border-color: var(--accent); color: var(--accent); background: rgba(91,141,239,0.12);">Restarting the bot…</div>

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
    <button class="btn accent" onclick="saveGlobalRiskRules()">Save</button>
    <span class="risk-saved" id="riskSaved">Saved.</span>
  </div>

  <h1>Strategies</h1>
  <p style="font-family:'JetBrains Mono', monospace; font-size:12px; color:var(--muted); margin-top:-6px;">
    Changes are saved immediately, but the bot doesn't pick them up until you click Restart Bot above.
  </p>
  <div id="strategyList"></div>
  <button class="add-btn" onclick="openModal(null)">+ Add strategy</button>

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
            <div class="field"><label>Line range min</label><input id="f_total_range_min" type="number" step="0.5"></div>
            <div class="field"><label>Line range max</label><input id="f_total_range_max" type="number" step="0.5"></div>
            <div class="field">
              <label>Direction (Over/Under only)</label>
              <select id="f_total_direction">
                <option value="">— not Over/Under —</option>
                <option value="Over">Over</option>
                <option value="Under">Under</option>
              </select>
            </div>
            <div class="field"><label>Max spread % (blank = global)</label><input id="f_max_spread_pct" type="number" step="0.1"></div>
            <div class="field"><label>Min liquidity (blank = global)</label><input id="f_min_liquidity_strat" type="number" step="0.01"></div>
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
        <div class="field">
          <label>Lookahead (minutes)</label>
          <input id="f_lookahead" type="number">
        </div>
        <div class="field"><label>Min seconds to start</label><input id="f_min_seconds" type="number"></div>

        <div class="field full">
          <div class="checkbox-row"><input type="checkbox" id="f_flashscore_enabled" onchange="onFlashscoreToggle()"><label style="margin:0;">Enable Flashscore</label></div>
        </div>
        <div class="field full" id="flashscore_step_field" style="display:none;">
          <label>Only allow from step</label>
          <input id="f_flashscore_min_step" type="number" min="1" step="1">
        </div>

        <div class="field full">
          <label>Which leagues can this strategy bet? <span style="color:var(--muted); text-transform:none;">— pick categories, or individual leagues. Leave both empty to allow any league.</span></label>
          <div class="subtabs">
            <div class="subtab-btn active" data-tab="f_tab_categories" onclick="switchTab('f','f_tab_categories')">Categories</div>
            <div class="subtab-btn" data-tab="f_tab_leagues" onclick="switchTab('f','f_tab_leagues')">Individual leagues</div>
          </div>
          <div id="f_tab_categories" class="subtab-pane active"></div>
          <div id="f_tab_leagues" class="subtab-pane"></div>
        </div>

        <div class="field full">
          <div class="checkbox-row"><input type="checkbox" id="f_enabled"><label style="margin:0;">Enabled</label></div>
        </div>
      </div>
      <div class="modal-actions">
        <button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn accent" onclick="saveStrategy()">Save</button>
      </div>
    </div>
  </div>

<script>
function toggleNav() { document.getElementById('navLinks').classList.toggle('open'); }
if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/service-worker.js').catch(() => {}); }

let strategies = [];
let editingIndex = null;
let marketRowCount = 0;
let allLeagues = [];
let allCategories = {};

const SPORTS_MARKETS = {
  "Football": [
    ["FOOTBALL_FULL_TIME_RESULT", "Full Time Result (1X2)"],
    ["FOOTBALL_FULL_TIME_RESULT_HANDICAP", "Full Time Result Handicap"],
    ["FOOTBALL_HALF_TIME_RESULT", "Half Time Result"],
    ["FOOTBALL_HALF_TIME_RESULT_HANDICAP", "Half Time Result Handicap"],
    ["FOOTBALL_OVER_UNDER_TOTAL_GOALS", "Total Goals Over/Under"],
    ["FOOTBALL_OVER_UNDER_HALF_TIME_TOTAL_GOALS", "Half Time Total Goals Over/Under"],
    ["FOOTBALL_OVER_UNDER_TOTAL_CORNERS", "Corners Over/Under"],
    ["FOOTBALL_BOTH_TEAMS_TO_SCORE", "Both Teams to Score"],
    ["FOOTBALL_FULL_TIME_CORRECT_SCORE", "Correct Score"],
    ["FOOTBALL_DOUBLE_CHANCE", "Double Chance"],
  ],
  "Basketball": [
    ["BBALL_MONEYLINE", "Moneyline"],
    ["BBALL_HALF_TIME_MONEYLINE", "Half Time Moneyline"],
  ],
  "Tennis": [
    ["TENNIS_WINNER", "Match Winner"],
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
    ["BASEBALL_MONEYLINE", "Moneyline"],
    ["BASEBALL_OVER_UNDER_TOTAL_RUNS", "Total Runs Over/Under"],
    ["BASEBALL_HANDICAP", "Handicap"],
  ],
};

function showError(msg) { const box = document.getElementById('errorBox'); box.textContent = msg; box.style.display = 'block'; }

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
function onSportChange(sportSelectId, marketSelectId) { populateMarketSelect(sportSelectId, marketSelectId); }
function setSportMarketFromType(sportSelectId, marketSelectId, marketTypeId) {
  let sportFound = null;
  for (const [sport, list] of Object.entries(SPORTS_MARKETS)) {
    if (list.some(([id]) => id === marketTypeId)) { sportFound = sport; break; }
  }
  if (!sportFound) {
    sportFound = Object.keys(SPORTS_MARKETS)[0];
    if (marketTypeId) showError(`Warning: "${marketTypeId}" isn't in the known market type list.`);
  }
  document.getElementById(sportSelectId).value = sportFound;
  populateMarketSelect(sportSelectId, marketSelectId);
  if (marketTypeId) document.getElementById(marketSelectId).value = marketTypeId;
}

// ---------- Global risk ----------
function fetchGlobalRiskRules() {
  fetch('api/global_risk_rules').then(r => r.json()).then(rules => {
    document.getElementById('g_max_spread_pct').value = rules.max_spread_pct ?? '';
    document.getElementById('g_min_liquidity').value = rules.minimum_liquidity ?? '';
  });
}
function saveGlobalRiskRules() {
  const maxSpreadRaw = document.getElementById('g_max_spread_pct').value.trim();
  const minLiqRaw = document.getElementById('g_min_liquidity').value.trim();
  const rules = { max_spread_pct: maxSpreadRaw === '' ? null : parseFloat(maxSpreadRaw), minimum_liquidity: minLiqRaw === '' ? null : parseFloat(minLiqRaw) };
  fetch('api/global_risk_rules', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rules) })
    .then(r => r.json()).then(result => {
      if (result.error) { alert('Could not save: ' + result.error); return; }
      const saved = document.getElementById('riskSaved');
      saved.style.display = 'inline';
      setTimeout(() => { saved.style.display = 'none'; }, 2000);
    }).catch(err => alert('Save failed: ' + err));
}

// ---------- Leagues / categories ----------
// Loaded once per page and cached, so the modal always renders from a
// fresh copy. leagueDataOk guards saveStrategy: if this never loaded,
// the checkboxes render empty and saving would silently wipe a
// strategy's league filter.
let leagueDataPromise = null;
let leagueDataOk = false;
function escHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function fetchLeagueData() {
  if (leagueDataPromise) return leagueDataPromise;
  leagueDataPromise = Promise.all([
    fetch('api/leagues').then(r => r.json()),
    fetch('api/league_categories').then(r => r.json())
  ]).then(([l, c]) => {
    allLeagues = Array.isArray(l) ? l : [];
    allCategories = c || {};
    leagueDataOk = true;
  }).catch(err => {
    leagueDataPromise = null;   // drop it so the next call retries
    leagueDataOk = false;
    throw err;
  });
  return leagueDataPromise;
}
function renderCategoryCheckboxes(containerId, selected) {
  const box = document.getElementById(containerId);
  const names = Object.keys(allCategories).sort();
  if (!names.length) { box.innerHTML = '<span style="color:var(--muted); font-size:12px;">No categories yet — add on the Leagues tab.</span>'; return; }
  box.innerHTML = names.map(cat => {
    const checked = selected.includes(cat) ? 'checked' : '';
    const id = containerId + '_' + cat.replace(/[^a-zA-Z0-9]/g,'_');
    return `<div class="checkbox-row" style="margin-bottom:4px;">
      <input type="checkbox" id="${id}" data-category="${cat.replace(/"/g,'&quot;')}" ${checked}>
      <label for="${id}" style="margin:0; font-family:'JetBrains Mono', monospace; font-size:12px;">${escHtml(cat)} (${(allCategories[cat]||[]).length})</label>
    </div>`;
  }).join('');
}
function renderLeagueCheckboxes(containerId, selected) {
  const box = document.getElementById(containerId);
  if (!allLeagues.length) { box.innerHTML = '<span style="color:var(--muted); font-size:12px;">No leagues known yet.</span>'; return; }
  box.innerHTML = `<input type="text" id="${containerId}_filter" placeholder="Filter leagues…" oninput="filterChecks('${containerId}', this.value)" style="width:100%; margin-bottom:6px;">` +
    allLeagues.map(name => {
    const checked = selected.includes(name) ? 'checked' : '';
    const id = containerId + '_' + name.replace(/[^a-zA-Z0-9]/g,'_');
    return `<div class="checkbox-row" style="margin-bottom:4px;">
      <input type="checkbox" id="${id}" data-league="${name.replace(/"/g,'&quot;')}" ${checked}>
      <label for="${id}" style="margin:0; font-family:'JetBrains Mono', monospace; font-size:12px;">${escHtml(name)}</label>
    </div>`;
  }).join('');
}
// Hides checkbox rows that don't match — checked ones are always kept
// visible so a filter can never hide (and thus silently drop) a selection.
function filterChecks(containerId, term) {
  const box = document.getElementById(containerId);
  const checkboxes = Array.from(box.querySelectorAll('input[type="checkbox"]'));
  const t = (term || '').trim().toLowerCase();
  let shown = 0;
  checkboxes.forEach(cb => {
    const row = cb.parentElement;
    const text = (cb.dataset.category || cb.dataset.league || '').toLowerCase();
    const match = !t || text.includes(t) || cb.checked;
    row.style.display = match ? '' : 'none';
    if (match) shown++;
  });
  let msg = document.getElementById(containerId + '_nomatch');
  if (!shown) {
    if (!msg) {
      msg = document.createElement('div');
      msg.id = containerId + '_nomatch';
      msg.style.cssText = 'color:var(--muted); font-size:12px;';
      msg.textContent = 'No leagues match that filter.';
      box.appendChild(msg);
    }
  } else if (msg) {
    msg.remove();
  }
}

function getCheckedCategories(containerId) {  return Array.from(document.querySelectorAll(`#${containerId} input[type="checkbox"]:checked`)).map(el => el.dataset.category);
}
function getCheckedLeagues(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} input[type="checkbox"]:checked`)).map(el => el.dataset.league);
}
function switchTab(prefix, tabId) {
  document.querySelectorAll(`.subtab-btn[data-tab^="${prefix}_tab_"]`).forEach(b => b.classList.remove('active'));
  document.querySelector(`.subtab-btn[data-tab="${tabId}"]`).classList.add('active');
  document.querySelectorAll(`#${prefix}_tab_categories, #${prefix}_tab_leagues`).forEach(p => p.classList.remove('active'));
  document.getElementById(tabId).classList.add('active');
}

// ---------- Strategies list / modal ----------
function fetchStrategies() {
  fetch('api/strategies').then(r => r.json()).then(data => {
    strategies = Array.isArray(data) ? data : [];
    renderList();
  });
}
function sortedStrategies() {
  return strategies
    .map((s, i) => ({ s, i }))
    .sort((a, b) => {
      // active first, then inactive
      const aRank = a.s.enabled ? 0 : 1;
      const bRank = b.s.enabled ? 0 : 1;
      if (aRank !== bRank) return aRank - bRank;
      return String(a.s.name || '').localeCompare(String(b.s.name || ''), undefined, { sensitivity: 'base' });
    });
}
function renderList() {
  let html = '';
  sortedStrategies().forEach(({ s, i }) => {
    const badgeClass = s.enabled ? 'on' : 'off';
    const badgeText = s.enabled ? 'ACTIVE' : 'INACTIVE';
    let metaLine;
    if (s.strategy_mode === 'multi_market') {
      metaLine = 'MULTI: ' + (s.market_configs || []).map(c => c.market_type_id).join(', ');
    } else {
      metaLine = `${s.market_type_id} · odds ${s.min_back_odds}-${s.max_back_odds}`;
      if (s.total_direction) metaLine += ` · ${s.total_direction} ${s.total_range}`;
    }
    if (s.strategy_type === 'compound') metaLine += ` · compound ${s.compound_start} -> ${s.compound_target}`;
    metaLine += ` · ${s.live_mode || 'pre'}`;
    metaLine += ` · lookahead ${s.event_lookahead_minutes ?? 180}m`;
    const cats = s.included_categories || [], leaguesFallback = s.included_leagues || [];
    if (cats.length || leaguesFallback.length) {
      const parts = [];
      if (cats.length) parts.push(cats.join(', '));
      if (leaguesFallback.length) parts.push(leaguesFallback.length + ' league(s)');
      metaLine += ' · leagues: ' + parts.join(' + ');
    }
    metaLine += s.flashscore_enabled ? ` · flashscore from step ${s.flashscore_min_step ?? 1}` : ` · flashscore off`;

    html += `<div class="card strat-row">
      <div>
        <div class="strat-name"><span class="badge ${badgeClass}">${badgeText}</span>${s.name}</div>
        <div class="strat-meta">${metaLine}</div>
      </div>
      <div class="row-actions">
        <button class="btn" onclick="openModal(${i})">Edit</button>
        <button class="btn" onclick="toggleActive(${i})">${s.enabled ? 'Disable' : 'Enable'}</button>
        <button class="btn reset" onclick="resetState('${escAttr(s.name)}')">Reset state</button>
        <button class="btn danger" onclick="removeStrategy(${i})">Remove</button>
      </div>
    </div>`;
  });
  document.getElementById('strategyList').innerHTML = html || '<p class="empty-note">No strategies yet.</p>';
}
function escAttr(name) { return String(name).replace(/'/g, "\\'"); }

const MARKET_ROW_TEMPLATE = (idx, data={}) => `
  <div class="market-row" id="market_row_${idx}">
    <button onclick="removeMarketRow(${idx})" style="position:absolute;top:8px;right:10px;background:none;border:none;color:var(--muted);cursor:pointer;">✕</button>
    <div class="field-grid" style="grid-template-columns:1fr 1fr;">
      <div class="field"><label>Sport</label><select class="mr_sport" id="mr_sport_${idx}" onchange="onSportChange('mr_sport_${idx}','mr_type_${idx}')"></select></div>
      <div class="field"><label>Market type</label><select class="mr_type" id="mr_type_${idx}"></select></div>
      <div class="field"><label>Min odds</label><input class="mr_min" type="number" step="0.01" value="${data.min_back_odds??1.45}"></div>
      <div class="field"><label>Max odds</label><input class="mr_max" type="number" step="0.01" value="${data.max_back_odds??1.6}"></div>
      <div class="field"><label>Total line</label><input class="mr_range" value="${data.total_range||''}"></div>
      <div class="field">
        <label>Direction</label>
        <select class="mr_dir">
          <option value="" ${!data.total_direction?'selected':''}>— none —</option>
          <option value="Over" ${data.total_direction==='Over'?'selected':''}>Over</option>
          <option value="Under" ${data.total_direction==='Under'?'selected':''}>Under</option>
        </select>
      </div>
      <div class="field"><label>Max spread % (blank=global)</label><input class="mr_spread" type="number" step="0.1" value="${data.max_spread_pct??''}"></div>
      <div class="field"><label>Min liquidity (blank=global)</label><input class="mr_liq" type="number" step="0.01" value="${data.minimum_liquidity??''}"></div>
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
function removeMarketRow(idx) { const el = document.getElementById('market_row_' + idx); if (el) el.remove(); }
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
function onFlashscoreToggle() {
  document.getElementById('flashscore_step_field').style.display = document.getElementById('f_flashscore_enabled').checked ? '' : 'none';
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

  if (isMulti) { (s.market_configs || [{}]).forEach(c => addMarketRow(c)); }
  else {
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

  document.getElementById('f_flashscore_enabled').checked = !!s.flashscore_enabled;
  document.getElementById('f_flashscore_min_step').value = s.flashscore_min_step ?? '';
  onFlashscoreToggle();

  const leagueCats = s.included_categories || [], leagueFallback = s.included_leagues || [];
  fetchLeagueData().then(() => {
    renderCategoryCheckboxes('f_tab_categories', leagueCats);
    renderLeagueCheckboxes('f_tab_leagues', leagueFallback);
  }).catch(() => {
    document.getElementById('f_tab_categories').innerHTML =
      '<span style="color:var(--loss); font-size:12px;">Could not load leagues/categories — close this dialog and reopen it before saving.</span>';
    document.getElementById('f_tab_leagues').innerHTML = '';
  });
  switchTab('f', 'f_tab_categories');

  document.getElementById('f_enabled').checked = s.enabled !== false;
  document.getElementById('modalBg').classList.add('open');
}
function closeModal() { document.getElementById('modalBg').classList.remove('open'); }

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

  const flashscoreEnabled = document.getElementById('f_flashscore_enabled').checked;
  let flashscoreMinStep = null;
  if (flashscoreEnabled) {
    flashscoreMinStep = parseInt(document.getElementById('f_flashscore_min_step').value);
    if (!Number.isInteger(flashscoreMinStep) || flashscoreMinStep < 1) { showError('Flashscore step must be 1 or higher.'); return; }
  }

  if (!leagueDataOk) {
    showError('The league/category list never loaded — reload the page and reopen this dialog, otherwise the league filter would be saved empty.');
    return;
  }
  const checkedCategories = getCheckedCategories('f_tab_categories');
  const checkedLeagues = getCheckedLeagues('f_tab_leagues');

  const updated = {
    ...existing, name,
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
    flashscore_enabled: flashscoreEnabled,
    flashscore_min_step: flashscoreEnabled ? flashscoreMinStep : null,
    included_categories: checkedCategories,
    included_leagues: checkedLeagues,
    enabled: document.getElementById('f_enabled').checked,
  };

  if (isMulti) {
    const rows = getMarketRows();
    if (!rows.length) { showError('Add at least one market row.'); return; }
    for (const r of rows) {
      if (!r.market_type_id) { showError('Every market row needs a market type id.'); return; }
      if (r.min_back_odds > r.max_back_odds) { showError('Min odds > max odds in a market row.'); return; }
      if (r.market_type_id.includes('OVER_UNDER') && (!r.total_range || !r.total_direction)) { showError(`Over/Under row (${r.market_type_id}) needs total line + direction.`); return; }
    }
    updated.market_configs = rows;
    updated.market_type_id = rows[0].market_type_id;
    updated.min_back_odds = rows[0].min_back_odds;
    updated.max_back_odds = rows[0].max_back_odds;
    updated.total_range = null; updated.total_direction = null;
    updated.max_spread_pct = null; updated.minimum_liquidity = null;
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
    if (isOverUnder && !hasExactLine && !hasRangeLine) { showError('Over/Under market needs a direction, plus a line or a range.'); return; }

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

  if (editingIndex === null) strategies.push(updated); else strategies[editingIndex] = updated;
  persist();
}
function toggleActive(index) { strategies[index].enabled = !strategies[index].enabled; persist(); }
function removeStrategy(index) {
  if (!confirm(`Remove strategy "${strategies[index].name}"? A backup is kept.`)) return;
  strategies.splice(index, 1); persist();
}
function resetState(name) {
  if (!confirm(`Reset saved state for "${name}"? This clears its balance/step progress.`)) return;
  fetch('api/reset_state/' + encodeURIComponent(name), { method: 'DELETE' })
    .then(r => r.json()).then(() => alert('State reset.')).catch(err => alert('Reset failed: ' + err));
}
function persist() {
  document.getElementById('savingBanner').style.display = 'block';
  fetch('api/strategies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(strategies) })
    .then(r => r.json()).then(result => {
      document.getElementById('savingBanner').style.display = 'none';
      if (result.error) { showError(result.error); return; }
      closeModal(); fetchStrategies();
    }).catch(err => { document.getElementById('savingBanner').style.display = 'none'; showError('Save failed: ' + err); });
}
function restartBot() {
  document.getElementById('restartBanner').style.display = 'block';
  fetch('api/restart_bot', { method: 'POST' }).then(r => r.json()).then(result => {
    document.getElementById('restartBanner').style.display = 'none';
    alert(result.restarted ? 'Bot restarted.' : ('Restart failed: ' + result.message));
  }).catch(err => { document.getElementById('restartBanner').style.display = 'none'; alert('Restart failed: ' + err); });
}

fetchGlobalRiskRules();
fetchLeagueData().catch(() => {});   // warm the cache; openModal retries on failure
fetchStrategies();
</script>
</body>
</html>
"""


def _to_athens(iso_str):
    """Times are saved as naive UTC. Converts to Athens time for display."""
    if not iso_str:
        return iso_str
    try:
        dt = datetime.fromisoformat(iso_str)
        dt = dt.replace(tzinfo=UTC_TZ).astimezone(ATHENS_TZ)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return iso_str


def _local_day(iso_str):
    """Naive-UTC ISO timestamp -> local calendar day (Europe/Athens).

    Returns a 'YYYY-MM-DD' string, or None when the value is unusable.
    """
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TZ)
    return dt.astimezone(ATHENS_TZ).strftime("%Y-%m-%d")


def query(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name TEXT,
            event_name TEXT,
            league TEXT,
            selection_name TEXT,
            price REAL,
            stake REAL,
            step INTEGER,
            placed_at TEXT,
            start_time TEXT,
            settled_at TEXT,
            result TEXT,
            profit REAL
        )
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(bets)").fetchall()]
    if "start_time" not in cols:
        conn.execute("ALTER TABLE bets ADD COLUMN start_time TEXT")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.route("/")
@require_password
def home():
    return render_template_string(PAGE, build=BUILD_VERSION)


@app.route("/analytics")
@require_password
def analytics_page():
    return render_template_string(ANALYTICS_PAGE, build=BUILD_VERSION)


@app.route("/categories")
@require_password
def categories_page():
    return render_template_string(CATEGORIES_PAGE, build=BUILD_VERSION)


@app.route("/api/balance")
@require_password
def api_balance():
    """BetDEX wallet balance (live from the exchange).

    Cached for BALANCE_TTL seconds so a page reload doesn't log in and
    hit the API every time. Failures return ok=False with the reason
    instead of an error status, so the tile on the dashboard can just
    show a dash.
    """
    now = time.time()
    with _balance_lock:
        cached = _balance_cache.get("data")
        if cached is not None and now - _balance_cache.get("at", 0) < BALANCE_TTL:
            return jsonify(cached)

    if not os.environ.get("BETDEX_WALLET_ID") or not os.environ.get("BETDEX_API_KEY"):
        data = {"ok": False, "error": "BetDEX credentials not configured for the dashboard."}
    else:
        client = BetdexClient()
        bal = client.get_wallet_balance()
        if bal:
            data = {"ok": True, **bal, "fetched_at": datetime.utcnow().isoformat() + "Z"}
        else:
            data = {"ok": False, "error": "Could not read the BetDEX wallet."}

    with _balance_lock:
        _balance_cache["data"] = data
        _balance_cache["at"] = now
    return jsonify(data)


@app.route("/api/summary")
@require_password
def summary():
    rows = query("""
        SELECT strategy_name,
               COUNT(*) as total,
               SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) as won,
               SUM(CASE WHEN result = 'lost' THEN 1 ELSE 0 END) as lost,
               SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) as pending,
               COALESCE(SUM(profit), 0) as profit,
               COALESCE(AVG(price), 0) as avg_odds
        FROM bets
        GROUP BY strategy_name
        ORDER BY strategy_name
    """)
    enabled_names = get_enabled_strategy_names()
    for r in rows:
        settled = r["won"] + r["lost"]
        r["win_rate"] = round(100 * r["won"] / settled, 1) if settled else 0
        r["enabled"] = True if enabled_names is None else (r["strategy_name"] in enabled_names)
        r["avg_odds"] = round(r["avg_odds"], 2)
        r["implied_win_rate"] = round(100 / r["avg_odds"], 1) if r["avg_odds"] else 0

    rows.sort(key=lambda r: (not r["enabled"], r["strategy_name"]))
    return jsonify(rows)


def _read_balance_for_strategy(name):
    """Reads the saved balance from that strategy's state file.
    If no state file yet (never placed a bet, or just Reset), falls back
    to the strategy's configured compound_start so the dashboard shows
    what balance it WILL start at, instead of a blank dash.
    Returns (balance, is_live) — is_live False means "not started yet".
    """
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    path = os.path.join(STATE_DIR, f"{safe_name}.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            bal = state.get("balance")
            if bal is not None:
                return bal, True
        except Exception:
            pass

    # No live balance — fall back to compound_start from strategy config.
    if os.path.isfile(STRATEGIES_FILE):
        try:
            with open(STRATEGIES_FILE, encoding="utf-8") as f:
                all_strategies = json.load(f).get("strategies", [])
            for s in all_strategies:
                if s.get("name") == name and s.get("strategy_type") == "compound":
                    return s.get("compound_start"), False
        except Exception:
            pass

    return None, False


@app.route("/api/profit_periods")
@require_password
def profit_periods():
    """Profit per day / month / year.

    Bucketed by the day the bet was PLACED, in Athens time - the same basis
    as the Today's Bets table, so the two always add up.
    """
    rows = query("""
        SELECT placed_at, profit
        FROM bets
        WHERE result IS NOT NULL AND placed_at IS NOT NULL
    """)

    daily, monthly, yearly = {}, {}, {}
    for r in rows:
        day = _local_day(r["placed_at"])
        if not day:
            continue
        profit = float(r["profit"] or 0)
        daily[day] = daily.get(day, 0.0) + profit
        monthly[day[:7]] = monthly.get(day[:7], 0.0) + profit
        yearly[day[:4]] = yearly.get(day[:4], 0.0) + profit

    cutoff = (datetime.now(ATHENS_TZ).date() - timedelta(days=6)).isoformat()
    return jsonify({
        "daily": [{"period": d, "profit": round(p, 4)} for d, p in sorted(daily.items()) if d >= cutoff],
        "monthly": [{"period": m, "profit": round(p, 4)} for m, p in sorted(monthly.items())],
        "yearly": [{"period": y, "profit": round(p, 4)} for y, p in sorted(yearly.items())],
    })


WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _empty_weekday_bucket(wd):
    return {
        "weekday": wd,
        "name": WEEKDAY_NAMES[wd],
        "bets": 0,
        "won": 0,
        "lost": 0,
        "profit": 0.0,
        "staked": 0.0,
        "price_sum": 0.0,
        "price_count": 0,
        "dates": set(),
    }


@app.route("/api/weekday_performance")
@require_password
def weekday_performance():
    """Performance broken down by day of the week (Mon..Sun).

    A weekday bucket spans many weeks, so the raw total is misleading: a
    weekday that happened to fall on more trading days shows more profit.
    Both the total and the average per occurrence are reported.
    """
    strategy = (request.args.get("strategy") or "").strip()

    sql = """
        SELECT settled_at, strategy_name, price, stake, result, profit
        FROM bets
        WHERE result IS NOT NULL
    """
    params = []
    if strategy:
        sql += " AND strategy_name = ?"
        params.append(strategy)
    rows = query(sql, tuple(params))

    buckets = {i: _empty_weekday_bucket(i) for i in range(7)}
    for r in rows:
        day = _local_day(r["settled_at"])
        if not day:
            continue
        b = buckets[datetime.fromisoformat(day).weekday()]  # 0=Mon .. 6=Sun
        b["bets"] += 1
        b["dates"].add(day)
        if r["result"] == "won":
            b["won"] += 1
        elif r["result"] == "lost":
            b["lost"] += 1
        b["profit"] += float(r["profit"] or 0)
        b["staked"] += float(r["stake"] or 0)
        if r["price"] is not None:
            b["price_sum"] += float(r["price"])
            b["price_count"] += 1

    weekdays = []
    for wd in range(7):
        b = buckets[wd]
        days = len(b["dates"])
        b["days"] = days
        del b["dates"]
        decided = b["won"] + b["lost"]
        b["win_rate"] = round(100 * b["won"] / decided, 1) if decided else 0.0
        b["roi"] = round(100 * b["profit"] / b["staked"], 1) if b["staked"] else 0.0
        b["avg_odds"] = round(b["price_sum"] / b["price_count"], 2) if b["price_count"] else 0.0
        b["profit"] = round(b["profit"], 4)
        b["staked"] = round(b["staked"], 4)
        b["avg_profit"] = round(b["profit"] / days, 4) if days else 0.0
        b["avg_bets"] = round(b["bets"] / days, 1) if days else 0.0
        weekdays.append(b)

    active = [w for w in weekdays if w["days"] > 0]
    total_profit = round(sum(w["profit"] for w in weekdays), 4)
    total_staked = round(sum(w["staked"] for w in weekdays), 4)
    total_bets = sum(w["bets"] for w in weekdays)
    total_won = sum(w["won"] for w in weekdays)
    total_lost = sum(w["lost"] for w in weekdays)
    total_days = sum(w["days"] for w in weekdays)

    best = max(active, key=lambda w: w["avg_profit"]) if active else None
    worst = min(active, key=lambda w: w["avg_profit"]) if active else None
    busiest = max(active, key=lambda w: w["bets"]) if active else None

    strategies = [r["strategy_name"] for r in query("""
        SELECT DISTINCT strategy_name FROM bets
        WHERE strategy_name IS NOT NULL
        ORDER BY strategy_name
    """)]

    return jsonify({
        "weekdays": weekdays,
        "summary": {
            "profit": total_profit,
            "staked": total_staked,
            "bets": total_bets,
            "won": total_won,
            "lost": total_lost,
            "win_rate": round(100 * total_won / (total_won + total_lost), 1) if (total_won + total_lost) else 0.0,
            "roi": round(100 * total_profit / total_staked, 1) if total_staked else 0.0,
            "avg_profit": round(total_profit / total_days, 4) if total_days else 0.0,
            "trading_days": total_days,
            "best_day": best,
            "worst_day": worst,
            "busiest": busiest,
        },
        "strategy": strategy,
        "strategies": strategies,
        "timezone": "Europe/Athens",
    })


@app.route("/api/chart_data")
@require_password
def chart_data():
    cumulative = query("""
        SELECT settled_at, strategy_name, profit
        FROM bets
        WHERE result IS NOT NULL
        ORDER BY settled_at ASC
    """)

    odds_buckets = query("""
        SELECT
          CASE
            WHEN price < 1.5 THEN '< 1.50'
            WHEN price < 1.55 THEN '1.50-1.54'
            WHEN price < 1.6 THEN '1.55-1.59'
            ELSE '1.60+'
          END as bucket,
          COUNT(*) as total,
          SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) as won
        FROM bets
        WHERE result IS NOT NULL
        GROUP BY bucket
    """)

    weekday = query("""
        SELECT
          CASE CAST(strftime('%w', placed_at) AS INTEGER)
            WHEN 0 THEN 'Sun' WHEN 1 THEN 'Mon' WHEN 2 THEN 'Tue'
            WHEN 3 THEN 'Wed' WHEN 4 THEN 'Thu' WHEN 5 THEN 'Fri'
            ELSE 'Sat'
          END as day,
          CAST(strftime('%w', placed_at) AS INTEGER) as day_num,
          COUNT(*) as total
        FROM bets
        GROUP BY day, day_num
        ORDER BY day_num
    """)

    stake_vs_profit = query("""
        SELECT strategy_name, stake, profit, result
        FROM bets
        WHERE result IS NOT NULL
    """)

    market_mix = query("""
        SELECT strategy_name, COUNT(*) as total
        FROM bets
        GROUP BY strategy_name
    """)

    profit_per_strategy = query("""
        SELECT strategy_name, COALESCE(SUM(profit), 0) as profit
        FROM bets
        WHERE result IS NOT NULL
        GROUP BY strategy_name
        ORDER BY profit DESC
    """)

    profit_per_league = query("""
        SELECT league,
               COUNT(*) as total,
               SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END) as won,
               COALESCE(SUM(profit), 0) as profit
        FROM bets
        WHERE result IS NOT NULL AND league IS NOT NULL
        GROUP BY league
        ORDER BY profit ASC
    """)
    for r in profit_per_league:
        r["win_rate"] = round(100 * r["won"] / r["total"], 1) if r["total"] else 0

    return jsonify({
        "cumulative": cumulative,
        "odds_buckets": odds_buckets,
        "weekday": weekday,
        "stake_vs_profit": stake_vs_profit,
        "market_mix": market_mix,
        "profit_per_strategy": profit_per_strategy,
        "profit_per_league": profit_per_league,
    })


@app.route("/api/pending")
@require_password
def pending():
    rows = query("""
        SELECT *, price AS odds FROM bets
        WHERE result IS NULL
        ORDER BY strategy_name, placed_at DESC
    """)
    for r in rows:
        r["start_time"] = _to_athens(r.get("start_time"))
        r["placed_at"] = _to_athens(r.get("placed_at"))
    return jsonify(rows)


@app.route("/api/today_bets")
@require_password
def today_bets():
    """The bets placed today (Athens day), settled or still open."""
    start_local = datetime.now(ATHENS_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(UTC_TZ).strftime("%Y-%m-%dT%H:%M:%S")
    rows = query("""
        SELECT *, price AS odds FROM bets
        WHERE placed_at >= ?
        ORDER BY placed_at DESC
    """, (start_utc,))
    for r in rows:
        r["placed_at"] = _to_athens(r.get("placed_at"))
    return jsonify(rows)


@app.route("/api/leagues")
@require_password
def get_leagues():
    return jsonify(get_seen_leagues(request.args.get("sport", "All")))


@app.route("/api/leagues_sports")
@require_password
def get_leagues_sports():
    return jsonify(get_league_sports())


@app.route("/api/league_categories", methods=["GET"])
@require_password
def get_league_categories():
    return jsonify(load_categories())


@app.route("/api/league_categories", methods=["POST"])
@require_password
def save_league_categories():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Categories must be an object of category name -> list of leagues."}), 400
    for cat, leagues in data.items():
        if not isinstance(leagues, list):
            return jsonify({"error": f"Category '{cat}' must contain a list of league names."}), 400
    save_categories(data)
    return jsonify({"saved": True})


@app.route("/icon-192.png")
def pwa_icon_192():
    return Response(base64.b64decode(ICON_192_B64), mimetype="image/png")


@app.route("/icon-512.png")
def pwa_icon_512():
    return Response(base64.b64decode(ICON_512_B64), mimetype="image/png")


@app.route("/service-worker.js")
def pwa_service_worker():
    # Minimal passthrough worker — just needs to exist for "Add to Home
    # Screen" / standalone install to be offered by the browser.
    js = "self.addEventListener('fetch', () => {});"
    return Response(js, mimetype="application/javascript")


@app.route("/api/strategies", methods=["GET"])
@require_password
def get_strategies():
    try:
        return jsonify(load_strategies_file())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/strategies", methods=["POST"])
@require_password
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
@require_password
def get_global_risk_rules():
    try:
        return jsonify(load_global_risk_rules())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/global_risk_rules", methods=["POST"])
@require_password
def save_global_risk_rules_route():
    rules = request.get_json(force=True)
    try:
        save_global_risk_rules(rules)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Could not save: {e}"}), 500
    return jsonify({"saved": True})


@app.route("/api/open_bets", methods=["GET"])
@require_password
def api_open_bets():
    try:
        return jsonify(get_open_bets())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/restart_bot", methods=["POST"])
@require_password
def restart_bot():
    ok, msg = restart_bot_container()
    return jsonify({"restarted": ok, "message": msg})


@app.route("/api/reset_state/<strategy_name>", methods=["DELETE"])
@require_password
def reset_state(strategy_name):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", strategy_name.strip())
    path = os.path.join(STATE_DIR, f"{cleaned}.json")
    if os.path.isfile(path):
        os.remove(path)
    return jsonify({"reset": True})


@app.route("/strategies")
@require_password
def strategies_page():
    return render_template_string(STRATEGIES_PAGE, build=BUILD_VERSION)


LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")


@app.route("/api/logs")
@require_password
def api_logs():
    which = request.args.get("file", "bot")
    lines = min(int(request.args.get("lines", 300)), 2000)
    fname = "skipped.log" if which == "skipped" else "bot.log"
    path = os.path.join(LOGS_DIR, fname)

    if not os.path.isfile(path):
        return jsonify({"lines": [], "error": f"{fname} not found yet"})

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        return jsonify({"lines": [ln.rstrip("\n") for ln in tail]})
    except Exception as e:
        return jsonify({"lines": [], "error": str(e)}), 500


LOGS_PAGE = """
<!DOCTYPE html>
<html>
<head>
<script>
document.write('<base href="' + (window.location.pathname.startsWith('/betdex') ? '/betdex/' : '/') + '">');
</script>
<meta charset="UTF-8">
<title>betdex // logs</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icon-192.png">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0a0e14">
<style>
  :root {
    --bg: #0a0e14; --card: #11161f; --card2: #141a25; --border: #1c2330; --border-thick: #2a3344;
    --text: #e4e7ec; --muted: #7a8699; --win: #2dd4a8; --loss: #ff6b5e; --pending: #f5b942; --accent: #5b8def;
  }
  * { box-sizing: border-box; }
  body { font-family: 'Inter', -apple-system, Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 28px 32px 60px; }
  .topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 22px; position: relative; flex-wrap: wrap; }
  .topbar h0 { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--muted); letter-spacing: 0.12em; text-transform: uppercase; }
  .nav-btn { font-family: 'Inter', sans-serif; font-size: 13px; font-weight: 600; color: var(--bg); background: var(--accent); border: none; border-radius: 8px; padding: 9px 16px; text-decoration: none; cursor: pointer; }
  .nav-btn.secondary { background: var(--card2); color: var(--text); border: 1px solid var(--border); }
  .hamburger { display: none; background: none; border: 1px solid var(--border); color: var(--text); font-size: 18px; border-radius: 8px; padding: 6px 12px; cursor: pointer; }
  .nav-links { display: flex; gap: 10px; flex-wrap: wrap; }
  @media (max-width: 760px) {
    .hamburger { display: block; }
    .nav-links { display: none; position: absolute; top: 48px; right: 0; flex-direction: column; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px; z-index: 100; min-width: 200px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
    .nav-links.open { display: flex; }
    .nav-links .nav-btn { width: 100%; text-align: left; }
  }
  .sub-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
  .sub-tab { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--muted); background: var(--card2); border: 1px solid var(--border); border-radius: 8px; padding: 8px 14px; cursor: pointer; }
  .sub-tab.active { color: var(--text); border-color: var(--accent); color: var(--accent); }
  .controls { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; flex-wrap: wrap; }
  .controls input, .controls select { background: var(--card2); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 7px 10px; font-family: 'JetBrains Mono', monospace; font-size: 12px; }
  .controls label { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
  .log-box { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; font-family: 'JetBrains Mono', monospace; font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; max-height: 75vh; overflow-y: auto; }
  .log-line { border-bottom: 1px solid rgba(255,255,255,0.03); padding: 2px 0; }
  .log-line.match { background: rgba(91,141,239,0.08); }
  .empty { color: var(--muted); padding: 20px 0; text-align: center; }
</style>
</head>
<body>

  <div class="topbar">
    <h0>betdex // logs <span style="opacity:0.35;">{{ build }}</span></h0>
    <button class="hamburger" onclick="toggleNav()">☰</button>
    <div class="nav-links" id="navLinks">
      <a class="nav-btn secondary" href="">Dashboard</a>
      <a class="nav-btn secondary" href="strategies">⚙ Manage Strategies</a>
      <a class="nav-btn secondary" href="categories">Leagues</a>
      <a class="nav-btn secondary" href="analytics">Analytics</a>
      <a class="nav-btn secondary" href="league_skips">League Skips</a>
      <button class="nav-btn" onclick="location.reload()">⟳ Refresh</button>
    </div>
  </div>

  <div class="sub-tabs">
    <div class="sub-tab active" data-file="bot">Bot Log</div>
    <div class="sub-tab" data-file="skipped">Skipped Log</div>
  </div>

  <div class="controls">
    <label>Lines: <select id="lineCount" onchange="load()">
      <option value="150">150</option>
      <option value="300" selected>300</option>
      <option value="800">800</option>
      <option value="2000">2000</option>
    </select></label>
    <input type="text" id="searchBox" placeholder="Filter (e.g. strategy name)..." oninput="render()" style="min-width:220px;">
    <label><input type="checkbox" id="autoRefresh" checked> auto-refresh (10s)</label>
  </div>

  <div class="log-box" id="logBox"><div class="empty">Loading…</div></div>

<script>
function toggleNav() { document.getElementById('navLinks').classList.toggle('open'); }

let currentFile = 'bot';
let rawLines = [];
let refreshTimer = null;

document.querySelectorAll('.sub-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    currentFile = tab.dataset.file;
    load();
  });
});

function load() {
  const lines = document.getElementById('lineCount').value;
  fetch(`/api/logs?file=${currentFile}&lines=${lines}`)
    .then(r => r.json())
    .then(data => {
      rawLines = data.lines || [];
      render();
    });
}

function render() {
  const filter = document.getElementById('searchBox').value.trim().toLowerCase();
  const box = document.getElementById('logBox');
  const filtered = filter ? rawLines.filter(l => l.toLowerCase().includes(filter)) : rawLines;

  if (!filtered.length) {
    box.innerHTML = '<div class="empty">No log lines yet.</div>';
    return;
  }

  const escape = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  box.innerHTML = filtered.map(l => `<div class="log-line${filter ? ' match' : ''}">${escape(l)}</div>`).join('');
  box.scrollTop = box.scrollHeight;
}

load();
setInterval(() => {
  if (document.getElementById('autoRefresh').checked) load();
}, 10000);
</script>
</body>
</html>
"""


@app.route("/logs")
@require_password
def logs_page():
    return render_template_string(LOGS_PAGE, build=BUILD_VERSION)


@app.route("/api/league_skips")
@require_password
def api_league_skips():
    """Parses skipped.log for 'league not allowed' skips and counts
    them per league. Optional ?category=Name filters to only strategies
    that use that category (via strategies.json included_categories).
    """
    category = request.args.get("category", "").strip()

    strategy_categories = {}
    try:
        with open(STRATEGIES_FILE, encoding="utf-8") as f:
            for s in json.load(f).get("strategies", []):
                strategy_categories[s.get("name")] = s.get("included_categories") or []
    except Exception:
        pass

    path = os.path.join(LOGS_DIR, "skipped.log")
    counts = {}
    if os.path.isfile(path):
        pattern = re.compile(r"\[([^\]]+)\] Skipped .* league '([^']+)' is not in this strategy's allowed leagues")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pattern.search(line)
                    if not m:
                        continue
                    strategy_name, league = m.group(1), m.group(2)
                    if category and category not in strategy_categories.get(strategy_name, []):
                        continue
                    counts[league] = counts.get(league, 0) + 1
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    rows = [{"league": k, "count": v, "reason": "Not in this strategy's allowed leagues"} for k, v in counts.items()]
    rows.sort(key=lambda r: r["league"])
    return jsonify(rows)


LEAGUE_SKIPS_PAGE = """
<!DOCTYPE html>
<html>
<head>
<script>
document.write('<base href="' + (window.location.pathname.startsWith('/betdex') ? '/betdex/' : '/') + '">');
</script>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>League Skips</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0a0e14">
<style>
  :root {
    --bg: #0a0e14; --card: #11161f; --card2: #141a25;
    --border: #1c2330; --text: #e4e7ec; --muted: #7a8699; --accent: #5b8def; --pending: #f5b942;
  }
  * { box-sizing: border-box; }
  body { font-family: 'Inter', -apple-system, Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 28px 32px 60px; }
  .topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; position: relative; flex-wrap: wrap; }
  .hamburger { display: none; background: none; border: 1px solid var(--border); color: var(--text); font-size: 18px; border-radius: 8px; padding: 6px 12px; cursor: pointer; }
  .nav-links { display: flex; gap: 10px; flex-wrap: wrap; }
  @media (max-width: 760px) {
    .hamburger { display: block; }
    .nav-links { display: none; position: absolute; top: 48px; right: 0; flex-direction: column; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px; z-index: 100; min-width: 200px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
    .nav-links.open { display: flex; }
    .nav-links .nav-btn { width: 100%; text-align: left; }
  }
  .topbar h0 { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--muted); letter-spacing: 0.12em; text-transform: uppercase; }
  .nav-btn { font-family: 'Inter', sans-serif; font-size: 13px; font-weight: 600; color: var(--bg); background: var(--accent); border: none; border-radius: 8px; padding: 9px 16px; text-decoration: none; cursor: pointer; }
  .nav-btn.secondary { background: var(--card2); color: var(--text); border: 1px solid var(--border); }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); font-size: 13.5px; }
  td { font-family: 'JetBrains Mono', monospace; }
  th { color: var(--muted); font-weight: 500; font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.04em; }
  select { background: var(--card2); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 8px 12px; font-family: 'Inter', sans-serif; font-size: 13px; margin-bottom: 16px; }
  .small { color: var(--muted); font-size: 11.5px; font-family: 'JetBrains Mono', monospace; }
</style>
</head>
<body>
  <div class="topbar">
    <h0>betdex // league skips <span style="opacity:0.35;">{{ build }}</span></h0>
    <button class="hamburger" onclick="toggleNav()">☰</button>
    <div class="nav-links" id="navLinks">
      <a class="nav-btn secondary" href="">Dashboard</a>
      <a class="nav-btn secondary" href="strategies">⚙ Manage Strategies</a>
      <a class="nav-btn secondary" href="categories">Leagues</a>
      <a class="nav-btn secondary" href="analytics">Analytics</a>
      <a class="nav-btn secondary" href="logs">Logs</a>
      <button class="nav-btn" onclick="location.reload()">⟳ Refresh</button>
    </div>
  </div>

  <p class="small" style="margin-bottom:14px;">Leagues that showed up but were skipped because they're not in a strategy's allowed league list.</p>

  <select id="categoryFilter" onchange="load()">
    <option value="">All categories</option>
  </select>

  <div class="card"><div id="skipsTable"></div></div>

<script>
function toggleNav() { document.getElementById('navLinks').classList.toggle('open'); }
if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/service-worker.js').catch(() => {}); }

fetch('api/league_categories').then(r => r.json()).then(cats => {
  const sel = document.getElementById('categoryFilter');
  Object.keys(cats).sort().forEach(c => {
    sel.insertAdjacentHTML('beforeend', `<option value="${c}">${c}</option>`);
  });
});

function load() {
  const category = document.getElementById('categoryFilter').value;
  const url = category ? `api/league_skips?category=${encodeURIComponent(category)}` : 'api/league_skips';
  fetch(url).then(r => r.json()).then(data => {
    if (!data.length) {
      document.getElementById('skipsTable').innerHTML = '<p class="small">No league skips found in the current log.</p>';
      return;
    }
    let html = '<table><tr><th>League</th><th>Reason</th><th>Times Skipped</th></tr>';
    data.forEach(r => {
      html += `<tr><td>${r.league}</td><td>${r.reason}</td><td>${r.count}</td></tr>`;
    });
    html += '</table>';
    document.getElementById('skipsTable').innerHTML = html;
  });
}

load();
</script>
</body>
</html>
"""


@app.route("/league_skips")
@require_password
def league_skips_page():
    return render_template_string(LEAGUE_SKIPS_PAGE, build=BUILD_VERSION)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8051)
