# BetDEX Trading Bot

An automated trading bot for the [BetDEX](https://www.betdex.com/) exchange, built on the Monaco Protocol. It allows for simultaneous execution of multiple betting strategies with customizable staking plans, compounding, and multi-market support.

## Features

- **Multi-Strategy Engine**: Run multiple independent strategies concurrently.
- **Staking Plans**: Supports ladder-based staking (e.g., Martingale) where the stake increases after a loss and resets after a win.
- **Compounding Mode**: Automatically reinvests winnings to reach a target balance.
- **Market Support**: Works with "Match Odds" (Full Time Result) and "Total Goals" (Over/Under) markets.
- **Multi-Market Strategies**: Ability to scan and bet across multiple market types within a single strategy.
- **Web Dashboard**: A built-in Flask dashboard to add, edit, enable/disable, and remove strategies without touching code.
- **Telegram Alerts**: Optional real-time notifications for bets placed and settled.
- **Robust Persistence**: Strategy progress and active bets are saved to disk, ensuring continuity across restarts.
- **Dockerized**: Easy deployment using Docker and Docker Compose.

## Prerequisites

- [Docker](https://www.docker.com/) and [Docker Compose](https://docs.docker.com/compose/) installed.
- BetDEX API credentials (`appId`, `walletId`, `apiKey`).
- (Optional) A Telegram Bot token and Chat ID for alerts.

## Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd betdex-bot
   ```

2. **Configure Environment Variables**:
   Copy `.env.example` to `.env` and fill in your BetDEX and Telegram credentials.
   ```bash
   cp .env.example .env
   ```
   Edit `.env`:
   ```env
   BETDEX_APP_ID=your_app_id
   BETDEX_WALLET_ID=your_wallet_id
   BETDEX_API_KEY=your_api_key
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

3. **Configure Strategies**:
   Initial strategies are defined in `config/strategies.json`. You can edit this file directly or use the dashboard later.

4. **Start the Bot**:
   ```bash
   docker-compose up -d
   ```
   This will start two containers:
   - `betdex_trading_bot`: The core engine running the strategies.
   - `betdex_dashboard`: The web interface for management.

## Usage

### Management Dashboard
Once the containers are running, access the dashboard at:
[http://localhost:8051](http://localhost:8051)

From here, you can:
- View all configured strategies.
- Toggle strategies on/off.
- Edit existing strategy parameters.
- Add new strategies.
- Restart the trading bot to apply changes.

### Monitoring Logs
Logs are stored in the `logs/` directory and are also available via Docker:
```bash
docker logs -f betdex_trading_bot
```

### Strategy Configuration
Each strategy in `config/strategies.json` supports several parameters:
- `name`: Unique identifier for the strategy.
- `enabled`: Boolean to activate/deactivate.
- `strategy_type`: `normal` (ladder/martingale) or `compound`.
- `strategy_mode`: `single` or `multi_market`.
- `market_type_id`: The BetDEX market identifier (e.g., `FOOTBALL_FULL_TIME_RESULT`).
- `staking_plan`: (For `normal` type) A list of stake amounts for each step.
- `compound_start`/`compound_target`: (For `compound` type) Starting amount and goal.
- `min_back_odds`/`max_back_odds`: Odds range for placing bets.
- `live_mode`: `pre` (pre-match), `live` (in-play), or `both`.
- `poll_interval_seconds`: Frequency of market scanning.

## Codebase Structure

- `src/main.py`: Entry point for the trading bot.
- `src/strategy_runner.py`: The core logic for scanning, betting, and settling.
- `src/api_client.py`: Wrapper for the BetDEX REST API.
- `src/dashboard.py`: Flask-based web management interface.
- `src/market_match_odds.py` & `src/market_total.py`: Matcher logic for different market types.
- `config/`: Stores strategy configuration, state, and the bet history database (`bets.db`).

## Disclaimer

This bot is for educational and research purposes. Automated trading carries significant risk. Use at your own risk. Always test with small stakes first.
