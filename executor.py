"""Order executor — translates signals into Binance API calls."""

import logging
import math
from typing import Any

import config
from binance_client import BinanceClient

log = logging.getLogger("executor")

ALGO_TYPES = {"STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT", "TRAILING_STOP_MARKET"}

DEFAULT_LEVERAGE = 10


async def get_symbol_price(client: BinanceClient, symbol: str) -> float:
    data = await client.get("/fapi/v1/ticker/price", {"symbol": symbol})
    return float(data["price"])


async def get_quantity_precision(client: BinanceClient, symbol: str) -> int:
    info = await client.get("/fapi/v1/exchangeInfo")
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                    return max(0, -int(math.log10(step)))
    return 0


async def get_usdt_balance(client: BinanceClient) -> float:
    balances = await client.get_signed("/fapi/v2/balance")
    for b in balances:
        if b["asset"] == "USDT":
            return float(b["availableBalance"])
    return 0.0


async def calc_quantity_from_risk(
    client: BinanceClient, symbol: str, stop_loss: float, entry_price: float | None = None
) -> float:
    """Size position so that hitting the SL loses exactly RISK_PCT of balance."""
    price = entry_price or await get_symbol_price(client, symbol)
    sl_distance = abs(price - stop_loss)
    if sl_distance == 0:
        raise ValueError("Stop loss cannot equal entry price")

    balance = await get_usdt_balance(client)
    risk_usdt = balance * config.RISK_PCT
    precision = await get_quantity_precision(client, symbol)

    raw_qty = risk_usdt / sl_distance
    qty = math.floor(raw_qty * 10**precision) / 10**precision
    log.info(
        f"Auto-sizing: balance=${balance:.2f}, risk={config.RISK_PCT*100:.1f}%"
        f" (${risk_usdt:.2f}), SL dist={sl_distance}, qty={qty}"
    )
    return qty


def _strip_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


async def set_leverage(client: BinanceClient, symbol: str, leverage: int) -> dict:
    log.info(f"Setting leverage {leverage}x for {symbol}")
    return await client.post_signed("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})


async def set_margin_type(client: BinanceClient, symbol: str, margin_type: str) -> dict:
    log.info(f"Setting margin type {margin_type} for {symbol}")
    try:
        return await client.post_signed(
            "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type}
        )
    except Exception as e:
        if "-4046" in str(e):
            log.info(f"Margin type already {margin_type}")
            return {"msg": f"Already {margin_type}"}
        raise


async def place_order(client: BinanceClient, order: dict) -> dict:
    order_type = order["order_type"]
    log.info(f"Placing {order_type} {order['side']} {order['symbol']} qty={order.get('quantity')}")

    if order_type in ALGO_TYPES:
        params = _strip_none({
            "algoType": "CONDITIONAL",
            "symbol": order["symbol"],
            "side": order["side"],
            "type": order_type,
            "quantity": order.get("quantity"),
            "price": order.get("price"),
            "triggerPrice": order.get("stop_price"),
            "timeInForce": order.get("time_in_force"),
            "reduceOnly": str(order.get("reduce_only", False)).lower() if order.get("reduce_only") else None,
            "closePosition": str(order.get("close_position", False)).lower() if order.get("close_position") else None,
            "positionSide": order.get("position_side"),
            "callbackRate": order.get("callback_rate"),
        })
        result = await client.post_signed("/fapi/v1/algoOrder", params)
    else:
        params = _strip_none({
            "symbol": order["symbol"],
            "side": order["side"],
            "type": order_type,
            "quantity": order.get("quantity"),
            "price": order.get("price"),
            "stopPrice": order.get("stop_price"),
            "timeInForce": order.get("time_in_force"),
            "reduceOnly": str(order.get("reduce_only", False)).lower() if order.get("reduce_only") else None,
            "closePosition": str(order.get("close_position", False)).lower() if order.get("close_position") else None,
            "positionSide": order.get("position_side"),
        })
        result = await client.post_signed("/fapi/v1/order", params)

    log.info(f"Order placed: {result.get('orderId') or result.get('algoId')}")
    return result


async def cancel_all_orders(client: BinanceClient, symbol: str) -> dict:
    log.info(f"Cancelling all orders for {symbol}")
    try:
        regular = await client.delete_signed("/fapi/v1/allOpenOrders", {"symbol": symbol})
    except Exception:
        regular = {"msg": "no regular orders"}
    try:
        algo = await client.delete_signed("/fapi/v1/algoOpenOrders", {"symbol": symbol})
    except Exception:
        algo = {"msg": "no algo orders"}
    return {"regular": regular, "algo": algo}


async def get_open_orders(client: BinanceClient, symbol: str) -> list[dict]:
    return await client.get_signed("/fapi/v1/openOrders", {"symbol": symbol})


async def get_positions(client: BinanceClient, symbol: str | None = None) -> list[dict]:
    params = {"symbol": symbol} if symbol else {}
    data = await client.get_signed("/fapi/v2/positionRisk", params)
    return [p for p in data if float(p["positionAmt"]) != 0]


async def execute_signal(client: BinanceClient, signal: dict) -> dict[str, Any]:
    """Execute a complete trade signal: set leverage/margin, place entry + SL + TP."""
    results = {}
    symbol = signal["symbol"]
    side = signal["side"]
    close_side = "SELL" if side == "BUY" else "BUY"

    leverage = signal.get("leverage", DEFAULT_LEVERAGE)
    results["leverage"] = await set_leverage(client, symbol, leverage)

    if "margin_type" in signal:
        results["margin_type"] = await set_margin_type(client, symbol, signal["margin_type"])

    if "quantity" in signal:
        quantity = signal["quantity"]
    elif "stop_loss" in signal and config.RISK_PCT:
        quantity = await calc_quantity_from_risk(
            client, symbol, signal["stop_loss"], signal.get("entry_price")
        )
    else:
        raise ValueError("Signal must include 'quantity' or both 'stop_loss' and RISK_PCT config")

    results["entry"] = await place_order(client, {
        "symbol": symbol,
        "side": side,
        "order_type": signal.get("entry_type", signal.get("type", "MARKET")),
        "quantity": quantity,
        "price": signal.get("entry_price"),
        "time_in_force": "GTC" if signal.get("entry_type") == "LIMIT" else None,
    })

    if "stop_loss" in signal:
        results["stop_loss"] = await place_order(client, {
            "symbol": symbol,
            "side": close_side,
            "order_type": "STOP_MARKET",
            "stop_price": signal["stop_loss"],
            "close_position": True,
        })

    if "take_profit" in signal:
        results["take_profit"] = await place_order(client, {
            "symbol": symbol,
            "side": close_side,
            "order_type": "TAKE_PROFIT_MARKET",
            "stop_price": signal["take_profit"],
            "close_position": True,
        })

    return results


async def update_sl_tp(client: BinanceClient, signal: dict) -> dict[str, Any]:
    """Cancel existing SL/TP and place new ones."""
    results = {}
    symbol = signal["symbol"]
    side = signal["side"]
    close_side = "SELL" if side == "BUY" else "BUY"

    results["cancel"] = await cancel_all_orders(client, symbol)

    if "stop_loss" in signal:
        results["stop_loss"] = await place_order(client, {
            "symbol": symbol,
            "side": close_side,
            "order_type": "STOP_MARKET",
            "stop_price": signal["stop_loss"],
            "close_position": True,
        })

    if "take_profit" in signal:
        results["take_profit"] = await place_order(client, {
            "symbol": symbol,
            "side": close_side,
            "order_type": "TAKE_PROFIT_MARKET",
            "stop_price": signal["take_profit"],
            "close_position": True,
        })

    return results
