"""Loads strategies.json — the one file you edit to add, remove, or
change strategies. Nothing else needs to change when you add a strategy.
"""

import asyncio
import json
import os

STRATEGIES_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "strategies.json")

# One lock, shared by every strategy runner in this process, so two
# strategies hitting balance 0 / target at the same moment can't both
# write strategies.json at once and corrupt it.
strategies_file_lock = asyncio.Lock()

# Risk-rule keys that can be set once at the top level of strategies.json
# under "global_risk_rules" and get applied to every strategy (and every
# market_config row, for multi_market strategies) that doesn't specify
# its own value. A strategy/market-config value always wins over the
# global one — the global value only fills the gap when the field is
# missing or null.
GLOBAL_RISK_RULE_KEYS = ("max_spread_pct", "minimum_liquidity")


def _apply_global_risk_defaults(cfg, global_rules):
    for key in GLOBAL_RISK_RULE_KEYS:
        if cfg.get(key) is None and global_rules.get(key) is not None:
            cfg[key] = global_rules[key]


async def disable_strategy(name, reason):
    """Sets enabled: false for one strategy in strategies.json. Does not
    auto re-enable it — you turn it back on by hand in the dashboard
    or the json file.
    """
    async with strategies_file_lock:
        with open(STRATEGIES_FILE, encoding="utf-8") as f:
            data = json.load(f)

        found = False
        for s in data.get("strategies", []):
            if s.get("name") == name:
                s["enabled"] = False
                found = True
                break

        if not found:
            print(f"[{name}] ⚠️ Could not find this strategy in strategies.json to disable it.")
            return

        tmp_path = STRATEGIES_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, STRATEGIES_FILE)

        print(f"[{name}] 🛑 Disabled in strategies.json ({reason}).")


def _validate_market_config(strat_name, cfg):
    market_type_id = cfg.get("market_type_id")
    if not market_type_id:
        raise ValueError(f"Strategy '{strat_name}': missing market_type_id.")
    if "OVER_UNDER" in market_type_id:
        has_exact_line = cfg.get("total_range") and cfg.get("total_direction")
        has_range_line = cfg.get("total_range_min") is not None and cfg.get("total_range_max") is not None and cfg.get("total_direction")
        if not has_exact_line and not has_range_line:
            raise ValueError(
                f"Strategy '{strat_name}': Over/Under market needs total_direction, "
                f"plus either total_range (exact line) or total_range_min/total_range_max (a range)."
            )


def load_strategies():
    """Returns the list of enabled, valid strategies from strategies.json.
    A strategy with a problem is skipped with a warning — it does not
    stop the other strategies from running.

    Before validation, any missing max_spread_pct / minimum_liquidity is
    filled in from the top-level "global_risk_rules" object (if present).
    """
    if not os.path.isfile(STRATEGIES_FILE):
        raise FileNotFoundError(f"Missing config: {STRATEGIES_FILE}")

    with open(STRATEGIES_FILE, encoding="utf-8") as f:
        data = json.load(f)

    global_rules = data.get("global_risk_rules", {})

    all_strategies = data.get("strategies", [])
    enabled = [s for s in all_strategies if s.get("enabled", True)]

    names = [s["name"] for s in enabled]
    if len(names) != len(set(names)):
        raise ValueError("Two enabled strategies have the same name. Names must be unique.")

    valid = []
    for s in enabled:
        try:
            if s.get("strategy_mode") == "multi_market":
                configs = s.get("market_configs", [])
                if not configs:
                    raise ValueError("multi_market strategy has no market_configs.")
                for cfg in configs:
                    _apply_global_risk_defaults(cfg, global_rules)
                    _validate_market_config(s["name"], cfg)
            else:
                _apply_global_risk_defaults(s, global_rules)
                _validate_market_config(s["name"], s)

            if s.get("strategy_type") == "compound":
                missing = [k for k in ("compound_start", "compound_target") if not s.get(k)]
                if missing:
                    raise ValueError(f"compound strategy missing: {', '.join(missing)}.")
            else:
                ladder = s.get("staking_plan", [])
                steps = s.get("staking_steps", len(ladder))
                if len(ladder) != steps:
                    print(f"[{s['name']}] ⚠️ staking_steps ({steps}) doesn't match "
                          f"staking_plan length ({len(ladder)}). Using staking_plan length.")

            valid.append(s)
        except ValueError as e:
            print(f"[{s['name']}] ⚠️ SKIPPED — {e}")

    return valid
