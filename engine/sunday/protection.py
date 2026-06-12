"""Pure risk-view math (risk-monitor support).

The risk-monitor agent's most error-prone checks used to be LLM bookkeeping: joining
open orders back onto positions to spot a naked position, summing notionals into
account exposure, and tracking an equity high-water mark for drawdown. These helpers
make each of those a deterministic field the account router can serve instead.

Stdlib-only and side-effect free (invariant 6): unit-tested anywhere, no ccxt/fastapi.
"""

from __future__ import annotations


def classify_leg(order_type: str | None) -> str | None:
    """Classify a Binance order type as a take-profit / stop-loss trigger leg."""
    t = (order_type or "").upper()
    if "TAKE_PROFIT" in t:
        return "take_profit"
    if "STOP" in t:
        return "stop_loss"
    return None


def immediate_trigger(close_side: str | None, take_profit: bool,
                      trigger: float | None, mark: float | None) -> bool | None:
    """Would a reduce-only trigger leg fire the instant it lands? None = cannot judge.

    Binance's Algo Service ACCEPTS a conditional leg whose condition is already true
    and executes it immediately — unlike the legacy book's -2021 rejection — so a
    mispriced trigger silently market-closes the position (BUG-01/BUG-04). Fire
    conditions (inclusive both ways): STOP sell / TAKE_PROFIT buy fire when
    price ≤ trigger; STOP buy / TAKE_PROFIT sell fire when price ≥ trigger."""
    if not trigger or not mark or close_side not in ("buy", "sell"):
        return None
    fires_down = (close_side == "sell") != take_profit
    return mark <= trigger if fires_down else mark >= trigger


def protection(qty: float | None, legs: list[dict]) -> dict:
    """Whether a position's TP/SL trigger legs exist and the SL actually covers it.

    ``legs`` rows carry ``tp_sl`` (from ``classify_leg``), ``amount`` (origQty) and
    ``close_position`` (Binance closePosition orders have qty 0 but close the whole
    position). ``sl_qty_covers`` is False when stop legs exist but sum below the
    position size — a partially-protected position the agent would otherwise miss."""
    tp_legs = [l for l in legs if l.get("tp_sl") == "take_profit"]
    sl_legs = [l for l in legs if l.get("tp_sl") == "stop_loss"]
    covers = any(l.get("close_position") for l in sl_legs) or (
        sum(l.get("amount") or 0.0 for l in sl_legs) >= (qty or 0.0) * 0.999)
    return {
        "take_profit": bool(tp_legs),
        "stop_loss": bool(sl_legs),
        "sl_qty_covers": bool(sl_legs) and covers,
    }


def protection_detail(qty: float | None, legs: list[dict]) -> dict:
    """One symbol's protection view for GET /api/perp/protection (PRD-003 §2b).

    ``legs`` rows carry ``tp_sl`` (from ``classify_leg``) plus id / trigger_price /
    status / amount / close_position / ts. ``take_profit`` / ``stop_loss`` surface the
    *primary* leg of each kind — the newest by ``ts``, i.e. the latest intent — with
    ladder counts so extra legs aren't hidden; coverage math reuses ``protection``."""
    def newest(rows: list[dict]) -> dict | None:
        return max(rows, key=lambda l: l.get("ts") or 0) if rows else None
    tp = [l for l in legs if l.get("tp_sl") == "take_profit"]
    sl = [l for l in legs if l.get("tp_sl") == "stop_loss"]
    return {
        "take_profit": newest(tp),
        "stop_loss": newest(sl),
        "tp_legs": len(tp),
        "sl_legs": len(sl),
        "sl_qty_covers": protection(qty, legs)["sl_qty_covers"],
    }


def liq_distance_pct(mark: float | None, liquidation: float | None) -> float | None:
    """How far (% of mark) the mark price sits from liquidation; None when unknown
    (e.g. cross positions report no per-position liquidation price)."""
    if not mark or not liquidation or mark <= 0:
        return None
    return round(abs(mark - liquidation) / mark * 100.0, 2)


def exposure(position_rows: list[dict], equity: float | None) -> dict:
    """Account-level exposure aggregates from position rows carrying ``notional``."""
    total = sum(r.get("notional") or 0.0 for r in position_rows)
    pct = round(total / equity * 100.0, 2) if equity else None
    return {"total_notional": round(total, 2), "exposure_pct": pct}


def drawdown_pct(equity: float | None, high_water: float | None) -> float | None:
    """Drawdown from the high-water mark, floored at 0 (a new high is 0%, not negative)."""
    if equity is None or not high_water or high_water <= 0:
        return None
    return round(max(0.0, (high_water - equity) / high_water * 100.0), 2)
