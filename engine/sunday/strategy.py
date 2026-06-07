"""Strategy engine: compute a target position from the active strategy and
reconcile the live position to match it. Deterministic — the LLM never runs here.

milestone 1.0 strategies: `momentum` (EMA cross) and `flat`. `mean_reversion`
lands in 1.1.
"""

from __future__ import annotations

from . import exchange, risk, store
from .config import settings

STRATEGIES = {"momentum", "flat"}  # mean_reversion -> 1.1


def ema(values: list[float], period: int) -> float:
    k = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def compute_target(symbol: str, strategy: str) -> dict:
    """Return {'side': long|short|flat, 'rationale': str, 'indicators': dict}."""
    if strategy == "flat":
        return {"side": "flat", "rationale": "flat：空手", "indicators": {}}
    if strategy == "momentum":
        closes = [c[4] for c in exchange.fetch_ohlcv(symbol, settings.timeframe, settings.ema_slow + 50)]
        ef = ema(closes, settings.ema_fast)
        es = ema(closes, settings.ema_slow)
        side = "long" if ef > es else "short"
        cmp = ">" if ef > es else "<"
        rationale = (
            f"momentum：EMA{settings.ema_fast}={ef:.1f} {cmp} EMA{settings.ema_slow}={es:.1f}"
            f"（{settings.timeframe}）→ {side}"
        )
        return {"side": side, "rationale": rationale, "indicators": {"ema_fast": ef, "ema_slow": es, "close": closes[-1]}}
    raise ValueError(f"strategy '{strategy}' not available in milestone 1.0")


def _current_side(symbol: str) -> str:
    for p in exchange.fetch_positions():
        if p["symbol"] == exchange._sym(symbol) and p.get("contracts"):
            return p["side"]  # long | short
    return "flat"


def _exposure_usd(symbol: str) -> float:
    total = 0.0
    for p in exchange.fetch_positions():
        if p.get("contracts"):
            total += abs(float(p["contracts"]) * float(p.get("markPrice") or p.get("entryPrice") or 0))
    return total


def reconcile(symbol: str, set_by: str = "system") -> dict:
    """Make the live position match the active strategy's target."""
    strat = store.current_strategy(symbol)
    target = compute_target(symbol, strat)
    action = {"flat": "go_flat", "long": "open_long", "short": "open_short"}[target["side"]]
    store.record_signal(symbol, strat, target["indicators"], action)
    store.set_rationale(target["rationale"])

    mode = store.get_mode()
    current = _current_side(symbol)
    if target["side"] == current:
        return {"action": "noop", "side": current, "rationale": target["rationale"]}

    if current != "flat":  # close before flipping / going flat
        exchange.close_position(symbol)
        exchange.cancel_all_orders(symbol)
        store.close_open_positions(symbol)

    if target["side"] == "flat":
        return {"action": "flat", "rationale": target["rationale"]}

    if mode in ("safe", "halted"):  # frozen: no new entries
        return {"action": "frozen_no_entry", "mode": mode, "rationale": target["rationale"]}

    return _open(symbol, target["side"], strat, target["rationale"])


def _open(symbol: str, side: str, strategy: str, reason: str) -> dict:
    price = float(exchange.fetch_ticker(symbol)["last"])
    qty = round(settings.target_notional_usd / price, 3)
    risk.guard(symbol, qty, price, _exposure_usd(symbol))  # raises RiskRejected + logs if over

    exchange.set_leverage(symbol, settings.leverage)
    order_side = "buy" if side == "long" else "sell"
    od = exchange.place_market(symbol, order_side, qty)
    store.record_order(symbol, order_side, "market", qty, price, od.get("status") or "new", str(od.get("id")), strategy, reason)

    stop_px = risk.stop_price(side, price, settings.stop_pct)
    close_side = "sell" if side == "long" else "buy"
    exchange.set_stop(symbol, close_side, qty, stop_px)

    store.record_position_open(symbol, side, qty, price, stop_px, strategy, reason)
    return {"action": f"opened_{side}", "qty": qty, "entry": price, "stop": stop_px, "rationale": reason}


def halt(mode: str, reason: str) -> dict:
    """flat = close everything + stop; safe = freeze new entries (keep existing)."""
    if mode == "flat":
        exchange.close_position(settings.symbol)
        exchange.cancel_all_orders(settings.symbol)
        store.close_open_positions(settings.symbol)
        store.set_strategy(settings.symbol, "flat", f"halt(flat): {reason}", "system")
        store.set_mode("halted")
    elif mode == "safe":
        store.set_mode("safe")
    return {"mode": store.get_mode()}
