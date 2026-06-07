"""Sunday HTTP service.

T1: startup (db pool + redis + migrations), /manual, /health.
T2: /market (public), /positions, /pnl (private).
T3: /strategy + /halt levers, real /status (strategy + position + envelope).
"""

from __future__ import annotations

import logging
import pathlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Callable, TypeVar

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from . import exchange, risk, store, strategy
from .config import settings

log = logging.getLogger("sunday")
_MANUAL = pathlib.Path(__file__).resolve().parent / "manual.md"

T = TypeVar("T")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.connect()
    applied = store.run_migrations()
    log.info("migrations applied: %s", applied or "(up to date)")
    yield
    store.close()


app = FastAPI(title="Sunday", version="0.1.0", lifespan=lifespan)


def _require_key() -> None:
    if not settings.binance_testnet_key:
        raise HTTPException(503, "BINANCE_TESTNET_KEY not set — add it to engine/.env")


def _ex(fn: Callable[[], T]) -> T:
    """Run an exchange call; turn ccxt/network errors into a clean 502."""
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as e:  # external API — surface a clean error, not a 500 traceback
        raise HTTPException(502, f"exchange error: {type(e).__name__}: {str(e)[:300]}")


class StrategyReq(BaseModel):
    symbol: str = "BTCUSDT"
    strategy: str
    reason: str


class HaltReq(BaseModel):
    reason: str
    mode: str = "flat"  # flat | safe


@app.get("/manual", response_class=PlainTextResponse)
def manual() -> str:
    return _MANUAL.read_text()


@app.get("/status")
def status() -> dict:
    mode = store.get_mode()
    strat = store.current_strategy(settings.symbol)
    rationale = store.get_rationale() or "(尚無決策)"
    position = None
    exposure = equity = 0.0
    leverage = 0
    try:
        for p in exchange.fetch_positions():
            if p["symbol"] == exchange._sym(settings.symbol) and p.get("contracts"):
                position = {
                    "side": p["side"],
                    "qty": p["contracts"],
                    "entry": p.get("entryPrice"),
                    "mark": p.get("markPrice"),
                    "upnl": p.get("unrealizedPnl"),
                }
                exposure = abs(float(p["contracts"]) * float(p.get("markPrice") or 0))
                leverage = int(float(p.get("leverage") or settings.leverage))
        bal = exchange.fetch_balance()
        equity = float((bal.get("total") or {}).get("USDT") or 0.0)
    except Exception as e:
        rationale = f"{rationale} [status: exchange unreachable: {type(e).__name__}]"
    return {
        "alive": True,
        "mode": mode,
        "symbol": settings.symbol,
        "strategy": strat,
        "strategy_rationale": rationale,
        "position": position,
        "exposure_usd": exposure,
        "leverage": leverage,
        "equity": equity,
        "pnl_day": None,
        "last_event_ts": None,
        "swarm_heartbeat_ok": True,
    }


@app.get("/health")
def health() -> dict:
    db_ok = redis_ok = True
    try:
        assert store.pool is not None
        with store.pool.connection() as conn:
            conn.execute("SELECT 1")
    except Exception:
        db_ok = False
    try:
        assert store.rds is not None
        store.rds.ping()
    except Exception:
        redis_ok = False
    return {"db": db_ok, "redis": redis_ok}


@app.get("/market")
def market(symbol: str = "BTCUSDT", tf: str = "1h", limit: int = 200) -> dict:
    ohlcv = _ex(lambda: exchange.fetch_ohlcv(symbol, tf, limit))
    return {"symbol": symbol, "tf": tf, "ohlcv": ohlcv}


@app.get("/positions")
def positions() -> list[dict]:
    _require_key()
    raw = _ex(lambda: exchange.fetch_positions())
    return [
        {
            "symbol": p.get("symbol"),
            "side": p.get("side"),
            "qty": p.get("contracts"),
            "entry": p.get("entryPrice"),
            "mark": p.get("markPrice"),
            "upnl": p.get("unrealizedPnl"),
            "stop": None,
            "strategy": None,
            "entry_reason": None,
        }
        for p in raw
    ]


@app.get("/pnl")
def pnl(since: str | None = None) -> dict:
    _require_key()
    bal = _ex(lambda: exchange.fetch_balance())
    raw = _ex(lambda: exchange.fetch_positions())
    unrealized = sum(float(p.get("unrealizedPnl") or 0) for p in raw)
    equity = (bal.get("total") or {}).get("USDT")
    return {"realized": None, "unrealized": unrealized, "equity": equity, "equity_curve": []}


@app.post("/strategy")
def post_strategy(req: StrategyReq) -> dict:
    if req.strategy not in strategy.STRATEGIES:
        raise HTTPException(400, f"strategy must be one of {sorted(strategy.STRATEGIES)} (mean_reversion lands in 1.1)")
    _require_key()
    store.set_strategy(req.symbol, req.strategy, req.reason, set_by="leader")
    store.set_mode("active")
    try:
        result = strategy.reconcile(req.symbol, set_by="leader")
    except risk.RiskRejected as e:
        raise HTTPException(409, f"risk rejected: {e}")
    except Exception as e:
        raise HTTPException(502, f"exchange error: {type(e).__name__}: {str(e)[:300]}")
    return {
        "ok": True,
        "symbol": req.symbol,
        "strategy": req.strategy,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }


@app.post("/halt")
def post_halt(req: HaltReq) -> dict:
    if req.mode not in ("flat", "safe"):
        raise HTTPException(400, "mode must be 'flat' or 'safe'")
    _require_key()
    try:
        result = strategy.halt(req.mode, req.reason)
    except Exception as e:
        raise HTTPException(502, f"exchange error: {type(e).__name__}: {str(e)[:300]}")
    return {"ok": True, **result}
