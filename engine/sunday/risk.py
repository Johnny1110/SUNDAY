"""Deterministic risk fuses — NOT the LLM's job.

Reject orders that breach the hard envelope and log every rejection to
risk_events; compute exchange-native stop prices. This is the final line of
defence: it blocks an over-line order no matter which agent (or bug) requested it.
"""

from __future__ import annotations

from . import store
from .config import settings


class RiskRejected(Exception):
    """An order was blocked by a deterministic fuse."""


def guard(symbol: str, qty: float, price: float, current_exposure_usd: float) -> None:
    """Raise (and log) if the order breaches the envelope; return None if OK."""
    notional = qty * price
    if notional > settings.max_position_usd:
        _reject("size_cap", {"symbol": symbol, "notional": notional, "max": settings.max_position_usd})
    if current_exposure_usd + notional > settings.max_total_exposure_usd:
        _reject("exposure_cap", {"current": current_exposure_usd, "add": notional, "max": settings.max_total_exposure_usd})
    if settings.leverage > settings.max_leverage:
        _reject("leverage_cap", {"leverage": settings.leverage, "max": settings.max_leverage})


def _reject(kind: str, detail: dict) -> None:
    store.record_risk_event(kind, detail, action_taken="order_rejected")
    raise RiskRejected(f"{kind}: {detail}")


def stop_price(side: str, entry: float, stop_pct: float) -> float:
    """Stop below entry for a long, above for a short."""
    px = entry * (1 - stop_pct) if side == "long" else entry * (1 + stop_pct)
    return round(px, 1)
