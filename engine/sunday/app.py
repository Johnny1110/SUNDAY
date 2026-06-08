"""Sunday HTTP service.

T1: startup (db pool + redis + migrations), /manual, /health.
T2: /market (public), /positions, /pnl (private).
T3: /strategy + /halt levers, real /status.
T4: /heartbeat + background watcher (regime detect -> notify; dead-man watchdog).
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from . import advisor, engine, exchange, risk, store, strategy
from .config import settings
from .market import Candles

log = logging.getLogger("sunday")
_MANUAL = pathlib.Path(__file__).resolve().parent / "manual.md"
_DASHBOARD = pathlib.Path(__file__).resolve().parent / "dashboard.html"

T = TypeVar("T")
_watcher_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.connect()
    applied = store.run_migrations()
    log.info("migrations applied: %s", applied or "(up to date)")
    store.set_heartbeat()  # baseline so the watchdog doesn't fire at boot
    global _watcher_task
    _watcher_task = asyncio.create_task(_watch_loop())
    yield
    if _watcher_task is not None:
        _watcher_task.cancel()
    store.close()


async def _watch_loop() -> None:
    """Periodic cheap self-check: regime detection (-> webhook) + dead-man watchdog."""
    while True:
        try:
            await asyncio.sleep(settings.tick_interval_sec)
            await asyncio.to_thread(engine.tick)
        except asyncio.CancelledError:
            break
        except Exception as e:  # never let the watcher die
            log.warning("watcher tick error: %s", e)


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


def _parse_since(since: str | None) -> datetime | None:
    """Parse an ISO date/datetime; naive values are treated as UTC. None -> no bound."""
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class StrategyReq(BaseModel):
    symbol: str = "BTCUSDT"
    strategy: str
    reason: str


class HaltReq(BaseModel):
    reason: str
    mode: str = "flat"  # flat | safe


class CommentaryReq(BaseModel):
    author: str = "analyst"
    title: str | None = None
    body: str


class EnvelopeReq(BaseModel):
    max_position_usd: float
    max_total_exposure_usd: float
    max_leverage: float
    max_drawdown_pct: float
    stop_pct: float
    reason: str


@app.get("/manual", response_class=PlainTextResponse)
def manual() -> str:
    return _MANUAL.read_text()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    """Sunday-served execution dashboard (single self-contained page). D12: not in evva."""
    return _DASHBOARD.read_text()


@app.get("/status")
def status() -> dict:
    mode = store.get_mode()
    strat = store.current_strategy(settings.symbol)
    rationale = store.get_rationale() or "(尚無決策)"
    age = store.heartbeat_age()
    swarm_ok = age is None or age < settings.heartbeat_timeout_sec
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
        "last_event_ts": store.get_last_event_ts(),
        "swarm_heartbeat_ok": swarm_ok,
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


@app.get("/advisor")
def advisor_panel(symbol: str = "BTCUSDT") -> dict:
    """Decision-support panel: per-strategy votes + regime + funding + a recommendation —
    the agent's read-only tool for deciding whether/what to switch (PRD §2.1). Derived,
    so the agent reasons over computed features instead of raw OHLCV."""
    ohlcv = _ex(lambda: exchange.fetch_ohlcv(symbol, settings.timeframe, 200))
    funding = _ex(lambda: exchange.fetch_funding_rate(symbol))
    return advisor.advise(Candles.from_klines(ohlcv), funding, store.current_strategy(symbol), symbol=symbol)


@app.get("/positions")
def positions() -> list[dict]:
    _require_key()
    raw = _ex(lambda: exchange.fetch_positions())
    metas = store.open_positions_meta_map()  # DB row gives strategy/entry_reason/stop
    out = []
    for p in raw:
        meta: dict = {}
        for db_sym, m in metas.items():
            if exchange._sym(db_sym) == p.get("symbol"):
                meta = m
                break
        out.append(
            {
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "qty": p.get("contracts"),
                "entry": p.get("entryPrice"),
                "mark": p.get("markPrice"),
                "upnl": p.get("unrealizedPnl"),
                "stop": meta.get("stop_price"),
                "strategy": meta.get("strategy"),
                "entry_reason": meta.get("entry_reason"),
            }
        )
    return out


@app.get("/pnl")
def pnl(since: str | None = None) -> dict:
    """Realized (DB) + live unrealized/equity (best-effort) + equity_curve (DB).

    Read-only dashboard endpoint: curve + realized come from postgres and render
    even if the exchange is unreachable; equity/unrealized are live extras.
    """
    since_dt = _parse_since(since)
    if since_dt is None:
        since_dt = datetime.now(timezone.utc) - timedelta(days=30)
        window_days = 30
    else:
        window_days = max(1, round((datetime.now(timezone.utc) - since_dt).total_seconds() / 86400))
    curve = store.equity_curve(since_dt)
    realized = store.realized_total(since_dt)
    equity = unrealized = None
    try:
        bal = exchange.fetch_balance()
        equity = (bal.get("total") or {}).get("USDT")
        unrealized = sum(float(p.get("unrealizedPnl") or 0) for p in exchange.fetch_positions())
    except Exception:
        if curve:
            equity = curve[-1][1]
    return {
        "realized": round(realized, 4),
        "unrealized": unrealized,
        "equity": equity,
        "equity_curve": curve,
        "window_days": window_days,
    }


@app.get("/performance")
def get_performance(since: str | None = None) -> list[dict]:
    """Per-strategy attribution: realized_pnl / n_trades / win_rate / avg_pnl / open_qty."""
    return store.performance(_parse_since(since))


@app.get("/strategy_history")
def get_strategy_history(since: str | None = None) -> list[dict]:
    """Strategy-switch timeline (for the dashboard's reason overlay)."""
    return store.strategy_history(_parse_since(since))


@app.post("/strategy")
def post_strategy(req: StrategyReq) -> dict:
    if req.strategy not in strategy.STRATEGIES:
        raise HTTPException(400, f"strategy must be one of {sorted(strategy.STRATEGIES)}")
    _require_key()
    store.set_strategy(req.symbol, req.strategy, req.reason, set_by="leader")
    store.set_mode("active")
    try:
        result = engine.reconcile(req.symbol, set_by="leader")
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
        result = engine.halt(req.mode, req.reason)
    except Exception as e:
        raise HTTPException(502, f"exchange error: {type(e).__name__}: {str(e)[:300]}")
    return {"ok": True, **result}


@app.get("/envelope")
def get_envelope() -> dict:
    """The active risk envelope (latest set), or the configured defaults if none set yet."""
    env = store.current_envelope()
    if env is None:
        env = {
            "max_position_usd": settings.max_position_usd,
            "max_total_exposure_usd": settings.max_total_exposure_usd,
            "max_leverage": float(settings.max_leverage),
            "max_drawdown_pct": settings.max_drawdown_pct,
            "stop_pct": settings.stop_pct,
        }
    return env


@app.post("/envelope")
def post_envelope(req: EnvelopeReq) -> dict:
    """Risk-envelope lever (leader only): set the hard caps the engine must obey.

    Takes effect on the next reconcile/tick: new entries are gated by the new caps
    and the drawdown breaker uses the new max_drawdown_pct. reason is stored (User-visible).
    """
    if not req.reason.strip():
        raise HTTPException(400, "reason is required — it is stored for the operator (PRD §7.11)")
    if min(req.max_position_usd, req.max_total_exposure_usd, req.max_leverage,
           req.max_drawdown_pct, req.stop_pct) <= 0:
        raise HTTPException(400, "envelope values must be positive")
    store.set_envelope(req.max_position_usd, req.max_total_exposure_usd, req.max_leverage,
                       req.max_drawdown_pct, req.stop_pct, req.reason, "leader")
    return {
        "ok": True,
        "envelope": {
            "max_position_usd": req.max_position_usd,
            "max_total_exposure_usd": req.max_total_exposure_usd,
            "max_leverage": req.max_leverage,
            "max_drawdown_pct": req.max_drawdown_pct,
            "stop_pct": req.stop_pct,
        },
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/heartbeat")
def heartbeat() -> dict:
    store.set_heartbeat()
    return {"ok": True, "watchdog_reset_at": datetime.now(timezone.utc).isoformat()}


@app.post("/commentary")
def post_commentary(req: CommentaryReq) -> dict:
    """analyst pushes a User-facing market note. Harmless write, NOT a trading lever."""
    cid = store.record_commentary(req.author, req.title, req.body)
    return {"ok": True, "id": cid}


@app.get("/commentary")
def get_commentary(since: str | None = None, limit: int = 50) -> list[dict]:
    return store.list_commentary(_parse_since(since), min(limit, 500))
