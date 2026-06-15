"""Trade bot — watches signals.json and executes trades on Binance Futures testnet.

Usage:
    python bot.py

Signal file format (signals.json):
    {
        "action": "open_trade",       // "open_trade" | "update_sl_tp" | "close_trade"
        "symbol": "BTCUSDT",
        "side": "BUY",                // "BUY" (long) | "SELL" (short)
        "quantity": 0.01,
        "leverage": 10,
        "margin_type": "ISOLATED",
        "entry_type": "MARKET",       // "MARKET" | "LIMIT"
        "entry_price": null,          // required for LIMIT
        "stop_loss": 95000,
        "take_profit": 105000,
        "status": "pending"           // bot changes to "executed" or "failed"
    }
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
from binance_client import BinanceClient
from executor import cancel_all_orders, execute_signal, get_positions, place_order, update_sl_tp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / "bot.log"),
    ],
)
log = logging.getLogger("bot")

SIGNALS_FILE = Path(config.SIGNALS_FILE)


def read_signal() -> dict | None:
    if not SIGNALS_FILE.exists():
        return None
    try:
        data = json.loads(SIGNALS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("status") == "pending":
        return data
    return None


def write_signal(data: dict) -> None:
    SIGNALS_FILE.write_text(json.dumps(data, indent=2))


async def handle_signal(client: BinanceClient, signal: dict) -> None:
    action = signal.get("action", "open_trade")
    symbol = signal["symbol"]
    log.info(f"Processing signal: {action} {symbol}")

    try:
        if action == "open_trade":
            results = await execute_signal(client, signal)
        elif action == "update_sl_tp":
            results = await update_sl_tp(client, signal)
        elif action == "close_trade":
            side = signal["side"]
            close_side = "SELL" if side == "BUY" else "BUY"
            await cancel_all_orders(client, symbol)
            positions = await get_positions(client, symbol)
            if positions:
                qty = abs(float(positions[0]["positionAmt"]))
                results = await place_order(client, {
                    "symbol": symbol,
                    "side": close_side,
                    "order_type": "MARKET",
                    "quantity": qty,
                    "reduce_only": True,
                })
            else:
                results = {"msg": "no open position to close"}
        else:
            raise ValueError(f"Unknown action: {action}")

        signal["status"] = "executed"
        signal["result"] = str(results)
        signal["executed_at"] = datetime.now(timezone.utc).isoformat()
        log.info(f"Signal executed successfully: {action} {symbol}")

    except Exception as e:
        signal["status"] = "failed"
        signal["error"] = str(e)
        signal["failed_at"] = datetime.now(timezone.utc).isoformat()
        log.error(f"Signal failed: {e}")

    write_signal(signal)


async def main() -> None:
    log.info("=" * 60)
    log.info("Trade Bot starting — Binance Futures TESTNET")
    log.info(f"Watching: {SIGNALS_FILE}")
    log.info("=" * 60)

    client = BinanceClient(config.BINANCE_API_KEY, config.BINANCE_API_SECRET, config.BASE_URL)

    try:
        # Verify connectivity
        await client.get("/fapi/v1/ping")
        log.info("Connected to Binance testnet")

        while True:
            signal = read_signal()
            if signal:
                await handle_signal(client, signal)
            await asyncio.sleep(config.POLL_INTERVAL)

    except KeyboardInterrupt:
        log.info("Bot stopped by user")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
