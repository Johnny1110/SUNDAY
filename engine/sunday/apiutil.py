"""Shared router helpers — clean exchange-error mapping + float coercion.

ccxt raises a zoo of network/exchange exceptions; surface them as a single clean
502 (not a 500 traceback) so an agent gets an actionable error string. ``ex_call``
mirrors the helper the old app.py used, lifted here for reuse across routers.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from fastapi import HTTPException

from .config import settings

T = TypeVar("T")


def ex_call(fn: Callable[[], T]) -> T:
    """Run an exchange call; turn ccxt/network errors into a clean 502."""
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as e:  # external API — surface a clean error, not a 500 traceback
        raise HTTPException(502, f"exchange error: {type(e).__name__}: {str(e)[:300]}")


def require_trade_key() -> None:
    """Guard the testnet-account endpoints (positions/orders/perp): no key → 503."""
    if not settings.binance_testnet_key:
        raise HTTPException(503, "BINANCE_TESTNET_KEY not set — add it to engine/.env")


def to_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def agent_of(x_agent) -> str | None:
    """The caller's self-reported agent id from the X-Agent header (BUG-03 audit log).
    Only a real non-blank string counts — direct function calls (tests) leave the
    FastAPI ``Header`` default object in place, which must read as anonymous."""
    return x_agent.strip()[:64] if isinstance(x_agent, str) and x_agent.strip() else None
