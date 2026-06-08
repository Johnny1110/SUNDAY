"""Strategy engine: compute a target position from the active strategy and
reconcile the live position to match it. Plus the T4 regime watcher + dead-man
watchdog. Deterministic — the LLM never runs here.

milestone 1.0 strategies: `momentum` (EMA cross) and `flat`. `mean_reversion`
lands in 1.1.
"""

from __future__ import annotations

from . import events, exchange, risk, store
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


def _capture_realized(symbol: str) -> float:
    """Sum unrealizedPnl of open positions for `symbol` — the realized PnL the
    instant we close them (persisted onto the DB row for attribution)."""
    total = 0.0
    for p in exchange.fetch_positions():
        if p["symbol"] == exchange._sym(symbol) and p.get("contracts"):
            total += float(p.get("unrealizedPnl") or 0)
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
        realized = _capture_realized(symbol)
        exchange.close_position(symbol)
        exchange.cancel_all_orders(symbol)
        store.close_open_positions(symbol, realized_pnl=realized)

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
        realized = _capture_realized(settings.symbol)
        exchange.close_position(settings.symbol)
        exchange.cancel_all_orders(settings.symbol)
        store.close_open_positions(settings.symbol, realized_pnl=realized)
        store.set_strategy(settings.symbol, "flat", f"halt(flat): {reason}", "system")
        store.set_mode("halted")
    elif mode == "safe":
        store.set_mode("safe")
    return {"mode": store.get_mode()}


# --- regime watcher + dead-man watchdog (T4) -------------------------------

def _detect_regime_and_notify(symbol: str) -> dict:
    """Emit regime_shift on an EMA-cross flip (debounced via redis last_regime)."""
    try:
        target = compute_target(symbol, "momentum")
    except Exception as e:  # exchange/indicator failure -> tell the leader
        events.notify("engine_degraded", "engine degraded", f"無法取得行情/指標：{e}", to="leader")
        return {"degraded": str(e)}
    regime = target["side"]  # long | short
    last = store.get_last_regime()
    if regime != last:
        store.set_last_regime(regime)
        if last is not None:  # don't emit on the first-ever baseline
            events.notify(
                "regime_shift", f"regime shift → {regime}", target["rationale"],
                data=target["indicators"], to="leader",
            )
            return {"regime": regime, "shifted": True, "prev": last}
    return {"regime": regime, "shifted": False}


def _watchdog_check() -> dict:
    """If the swarm stopped heartbeating, enter safe-mode (freeze new entries)."""
    age = store.heartbeat_age()
    if age is not None and age > settings.heartbeat_timeout_sec and store.get_mode() == "active":
        store.set_mode("safe")
        events.notify(
            "safe_mode_entered", "safe-mode entered",
            f"swarm heartbeat 逾時 {int(age)}s（>{settings.heartbeat_timeout_sec}s），凍結新倉", to="leader",
        )
        return {"safe_mode": True, "age": age}
    return {"safe_mode": False, "age": age}


def _record_pnl_snapshot() -> dict:
    """Capture one equity-curve point. Skip (don't pollute the curve) on exchange error."""
    try:
        bal = exchange.fetch_balance()
        equity = float((bal.get("total") or {}).get("USDT") or 0.0)
        unrealized = sum(float(p.get("unrealizedPnl") or 0) for p in exchange.fetch_positions())
    except Exception as e:
        return {"recorded": False, "error": str(e)}
    realized = store.realized_total()
    peak = store.equity_peak()
    peak = max(peak, equity) if peak is not None else equity
    drawdown = round((peak - equity) / peak * 100, 4) if peak and peak > 0 else 0.0
    store.record_pnl_snapshot(equity, realized, unrealized, drawdown)
    return {"recorded": True, "equity": equity, "drawdown_pct": drawdown}


def tick() -> dict:
    """One periodic self-check (regime + watchdog + equity snapshot), run by the watcher loop."""
    sym = settings.symbol
    return {
        "regime": _detect_regime_and_notify(sym),
        "watchdog": _watchdog_check(),
        "pnl_snapshot": _record_pnl_snapshot(),
    }
