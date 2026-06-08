"""Binance USDⓈ-M perpetual **testnet** adapter (ccxt).

Public market data needs no key; balance/positions/orders need the testnet API
key in .env. Everything stays on testnet (set_sandbox_mode) — milestone 1.0
never touches mainnet.
"""

from __future__ import annotations

import ccxt

from .config import settings

_ex: ccxt.binanceusdm | None = None


def exchange() -> ccxt.binanceusdm:
    global _ex
    if _ex is None:
        ex = ccxt.binanceusdm(
            {
                "apiKey": settings.binance_testnet_key or None,
                "secret": settings.binance_testnet_secret or None,
                "enableRateLimit": True,
                # ccxt gates the (still-working) futures testnet behind this opt-in flag
                "options": {"defaultType": "future", "disableFuturesSandboxWarning": True},
            }
        )
        ex.set_sandbox_mode(True)  # USDⓈ-M futures testnet
        _ex = ex
    return _ex


def _sym(symbol: str) -> str:
    """Map an exchange id (BTCUSDT) to ccxt's unified symbol (BTC/USDT:USDT)."""
    ex = exchange()
    if not ex.markets:
        ex.load_markets()
    if symbol in ex.markets:
        return symbol
    m = ex.markets_by_id.get(symbol)
    if m:
        return (m[0] if isinstance(m, list) else m)["symbol"]
    return symbol


def to_unified(symbol: str) -> str:
    """Public: map an exchange id (BTCUSDT) → ccxt's unified symbol (BTC/USDT:USDT).
    The HTTP layer (app.py) uses this to match basket symbols against position rows
    without reaching into the private `_sym`."""
    return _sym(symbol)


def fetch_ohlcv(symbol: str, tf: str = "1h", limit: int = 200) -> list[list]:
    return exchange().fetch_ohlcv(_sym(symbol), timeframe=tf, limit=limit)


def fetch_ticker(symbol: str) -> dict:
    return exchange().fetch_ticker(_sym(symbol))


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fetch_funding_rate(symbol: str) -> float | None:
    """Current perp funding rate as a per-8h fraction (positive = longs pay shorts).
    A perps-specific edge the advisor factors in. None on unsupported/error."""
    try:
        fr = exchange().fetch_funding_rate(_sym(symbol))
        return _f(fr.get("fundingRate"))
    except Exception:
        return None


def fetch_funding_info(symbol: str) -> dict:
    """funding rate (per-8h) + mark/index price — for the info layer (basis = mark vs index).
    All fields None-tolerant (testnet sometimes omits them)."""
    try:
        fr = exchange().fetch_funding_rate(_sym(symbol))
        return {"rate": _f(fr.get("fundingRate")), "mark": _f(fr.get("markPrice")), "index": _f(fr.get("indexPrice"))}
    except Exception:
        return {"rate": None, "mark": None, "index": None}


def fetch_open_interest(symbol: str) -> float | None:
    """Open interest (USD value preferred, else contract amount). None on unsupported/error."""
    try:
        oi = exchange().fetch_open_interest(_sym(symbol))
        return _f(oi.get("openInterestValue")) or _f(oi.get("openInterestAmount"))
    except Exception:
        return None


def fetch_last_price(symbol: str) -> float | None:
    """Last traded price (for the buy-hold shadow baseline). None on error."""
    try:
        return _f(fetch_ticker(symbol).get("last"))
    except Exception:
        return None


def fetch_positions() -> list[dict]:
    return [p for p in exchange().fetch_positions() if p.get("contracts")]


def fetch_balance() -> dict:
    return exchange().fetch_balance()


def set_leverage(symbol: str, leverage: int) -> None:
    exchange().set_leverage(leverage, _sym(symbol))


def place_market(symbol: str, side: str, qty: float, reduce_only: bool = False) -> dict:
    params = {"reduceOnly": True} if reduce_only else {}
    return exchange().create_order(_sym(symbol), "market", side, qty, params=params)


def set_stop(symbol: str, close_side: str, qty: float, stop_price: float) -> dict:
    # close_side is opposite the position side; reduce-only stop-market.
    return exchange().create_order(
        _sym(symbol),
        "STOP_MARKET",
        close_side,
        qty,
        params={"stopPrice": stop_price, "reduceOnly": True},
    )


def close_position(symbol: str) -> dict | None:
    target = _sym(symbol)
    for p in fetch_positions():
        if p["symbol"] == target and p.get("contracts"):
            close_side = "sell" if p["side"] == "long" else "buy"
            return place_market(symbol, close_side, p["contracts"], reduce_only=True)
    return None


def cancel_all_orders(symbol: str) -> None:
    exchange().cancel_all_orders(_sym(symbol))
