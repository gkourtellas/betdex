"""Main entry point. Starts every enabled strategy from strategies.json,
all running at the same time, independently.

To add, remove, or change a strategy: edit config/strategies.json only.
No code changes needed.
"""

import asyncio

from api_client import BetdexClient
from log_util import install_print_logger, setup_logging
from strategy_loader import load_strategies
from strategy_runner import StrategyRunner


async def main():
    log_path = setup_logging()
    install_print_logger()
    print(f"Log file: {log_path}")

    client = BetdexClient()
    if not client.login():
        print("Initial login failed. Stopping.")
        return

    strategies = load_strategies()
    if not strategies:
        print("No enabled strategies found in strategies.json. Nothing to do.")
        return

    print(f"Loaded {len(strategies)} strategy(ies): {', '.join(s['name'] for s in strategies)}")

    runners = [StrategyRunner(s, client) for s in strategies]
    await asyncio.gather(*(r.run() for r in runners))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
