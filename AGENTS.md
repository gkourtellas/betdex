# Agent Information for BetDEX Bot

## Codebase Overview

This project is a Python-based automated trading bot for the BetDEX exchange. It's designed to be modular and highly configurable.

### Core Components

- **`api_client.py`**: Handles all interactions with the BetDEX REST API. It includes session management, market fetching, order submission, and Telegram alerts.
- **`strategy_runner.py`**: The heart of the bot. Each strategy runs in its own `StrategyRunner` instance. It manages the lifecycle of a strategy: scanning for opportunities, placing bets, and checking for settlements.
- **`strategy_loader.py`**: Responsible for reading and validating the `config/strategies.json` file.
- **`state_store.py`**: Manages persistence for each strategy. State is stored in `config/state/<strategy_name>.json`.
- **`market_match_odds.py` & `market_total.py`**: Specialized modules for identifying betting opportunities in different market types.
- **`dashboard.py`**: A Flask web application for managing strategies. It interacts with the `docker` socket to restart the bot container.

## Key Concepts

- **Market Matching**: The bot compares available prices against strategy criteria. It specifically looks for "Against" prices in the API to place "For" (Back) bets, as confirmed by site behavior.
- **Staking**:
    - `normal`: Uses a predefined `staking_plan` (list of floats). It moves to the next step on loss and resets to step 1 on win.
    - `compound`: Reinvests the entire balance. It stops when it reaches `compound_target` or hits 0.
- **Multi-Market Mode**: A single strategy can monitor multiple `market_type_id`s simultaneously if `strategy_mode` is set to `multi_market`.

## Maintenance Tips

- **Adding New Market Types**: To support a new market type, create a new matcher module (similar to `market_total.py`) and update `_matcher_for` in `strategy_runner.py`.
- **API Changes**: BetDEX API updates should be reflected in `api_client.py`.
- **Dashboard Updates**: The dashboard is a single-file Flask app with embedded HTML/JavaScript.
- **Persistence**: Always use `state_store.py` for saving strategy-specific progress. For permanent bet records, use `bet_records.py` which writes to `config/bets.db`.

## Configuration

The primary configuration file is `config/strategies.json`. The bot reloads this file on startup. When modified through the dashboard, the bot container is automatically restarted to apply changes.
