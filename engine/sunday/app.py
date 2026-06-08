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
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from . import exchange, risk, store, strategy
from .config import settings

log = logging.getLogger("sunday")
_MANUAL = pathlib.Path(__file__).resolve().parent / "manual.md"
_DASHBOARD = pathlib.Path(__file__).resolve().parent / "dashboard.html"

T = TypeVar("T")
_watcher_task: asyncio.Task | None = None

SYMBOL = "BTCUSDT"            # Gate-1 single symbol (PRD §10 / milestone-1.0)
TIMEFRAME = "1h"
TICK_SECONDS = 60            # loop cadence; the strategy itself reads 1h bars
WATCHDOG_MINUTES = 90       # no swarm heartbeat for this long → safe-mode (PRD §7.6)
ENVELOPE = risk.DEFAULT_ENVELOPE


@dataclass
class EngineState:
    mode: str = "flat"                       # flat | running | safe | halt
    locked: bool = False                     # drawdown breaker latched
    symbol: str = SYMBOL
    peak_equity: float = 0.0
    last_regime_label: str | None = None
    last_event_ts: str | None = None
    last_candles: Candles | None = None
    stop: threading.Event = field(default_factory=threading.Event)


state = EngineState()
ex = BinanceUSDM.from_settings(settings)


# --- request bodies --------------------------------------------------------

class StrategyBody(BaseModel):
    symbol: str = SYMBOL
    strategy: str
    reason: str | None = None
    expected_current: str | None = None


class HaltBody(BaseModel):
    reason: str
    mode: str = "safe"          # safe (freeze new) | flat (close all)


# --- helpers ---------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _heartbeat_ok() -> bool:
    last = store.last_heartbeat()
    if last is None:
        return False
    return (_now() - last).total_seconds() < WATCHDOG_MINUTES * 60


def _current_side() -> tuple[str | None, dict | None]:
    """Book side from the exchange (truth), or None when flat/unreachable."""
    try:
        pos = ex.positions(state.symbol)
    except ExchangeError:
        return None, None
    if not pos:
        return None, None
    return pos[0]["side"], pos[0]


def _gather_status() -> dict:
    strat_name = store.current_strategy(state.symbol)
    candles = state.last_candles
    rationale = None
    if candles is not None:
        try:
            rationale = strategy.evaluate(strat_name, candles).rationale
        except ValueError:
            pass
    side, pos = _current_side()
    exposure = abs(float(pos["qty"]) * float(pos["mark"])) if pos else 0.0
    equity = 0.0
    try:
        equity = ex.wallet_equity_usdt()
    except ExchangeError:
        pass
    base = {
        "alive": True,
        "mode": state.mode,
        "symbol": state.symbol,
        "strategy": strat_name,
        "strategy_rationale": rationale,
        "position": pos,
        "exposure_usd": exposure,
        "leverage": (exposure / equity) if equity else 0.0,
        "equity": equity,
        "pnl_day": float(pos["upnl"]) if pos else 0.0,
        "drawdown_pct": risk.drawdown_pct(equity, state.peak_equity),
        "last_event_ts": state.last_event_ts,
        "swarm_heartbeat_ok": _heartbeat_ok(),
        "last_lever": store.last_lever(state.symbol),
    }
    return views.status_view(base, candles)


def _fire(event: dict) -> None:
    """Send a webhook and log it (never raises into the loop)."""
    status, ok = events.post(settings.evva_webhook_url, event)
    state.last_event_ts = _now().isoformat()
    try:
        store.record_webhook(event["data"]["event_type"], event.get("to") or "leader",
                             event.get("title", ""), event.get("body", ""), status, None)
    except Exception:  # logging a webhook must never break the loop
        log.exception("record_webhook failed")


# --- the trading loop ------------------------------------------------------

def tick() -> None:
    """One engine cycle. Wrapped by run_loop so a raised error degrades one tick."""
    symbol = state.symbol
    candles = ex.fetch_klines(symbol, TIMEFRAME, 200)
    state.last_candles = candles

    # 1) regime read → fire regime_shift only on a real change (PRD §5 event-gating)
    rr = regime.classify(candles)
    if regime.is_shift(state.last_regime_label, rr.label):
        _fire(events.regime_shift_event(state.last_regime_label, rr, _gather_status()))
    if rr.label != "unknown":
        state.last_regime_label = rr.label

    # 2) liveness: no swarm heartbeat → safe-mode floor (PRD §7.6 dead-man)
    if not _heartbeat_ok() and state.mode not in ("safe", "halt"):
        state.mode = "safe"
        _fire(events.build_event("safe_mode_entered", title="Safe-mode entered",
                                 body="swarm heartbeat 逾時，Sunday 凍結新倉（既有倉留 stop）。",
                                 status=_gather_status(), to="leader"))

    # 3) drawdown breaker (deterministic, non-LLM)
    try:
        equity = ex.wallet_equity_usdt()
        state.peak_equity = max(state.peak_equity, equity)
        dd = risk.check_drawdown(equity, state.peak_equity, ENVELOPE)
        if dd.breached and not state.locked:
            state.locked = True
            _flatten(reason="drawdown breaker")
            store.record_risk_event("drawdown", {"drawdown_pct": dd.drawdown_pct}, "flatten_and_lock")
            _fire(events.build_event("risk_breach", title="Risk breach: drawdown",
                                     body=dd.reason, status=_gather_status(), to="leader"))
    except ExchangeError:
        pass

    # 4) act on the active strategy (unless frozen/locked)
    if state.mode in ("safe", "halt") or state.locked:
        return
    state.mode = "running"
    _reconcile(candles)


def _reconcile(candles: Candles) -> None:
    """Bring the book in line with the active strategy's target, risk-gated."""
    symbol = state.symbol
    strat_name = store.current_strategy(symbol)
    target = strategy.target_side(strat_name, candles)
    side, _ = _current_side()
    action = execution.plan_transition(side, target)
    if action == execution.HOLD:
        return

    vote = strategy.evaluate(strat_name, candles) if strat_name != "flat" else None
    store.record_signal(symbol, strat_name, vote.indicators if vote else {}, action)
    price = candles.last_close or 0.0

    if action == execution.CLOSE or action.startswith("flip"):
        _flatten(reason=f"{action} ({strat_name})")
        if action == execution.CLOSE:
            return

    want = "long" if action in (execution.OPEN_LONG, execution.FLIP_LONG) else "short"
    _open(symbol, want, price, strat_name, vote.rationale if vote else "")


def _open(symbol: str, side: str, price: float, strat_name: str, reason: str) -> None:
    """Size within the envelope, gate, then place market entry + native stop."""
    ctx = risk.RiskContext(equity=_safe_equity(), current_exposure_usd=0.0)
    qty = round(risk.max_allowed_qty(price, ctx, ENVELOPE), 3)
    if qty <= 0:
        return
    order_side = "BUY" if side == "long" else "SELL"
    stop_side = "SELL" if side == "long" else "BUY"
    stop_price = round(price * (1 - ENVELOPE.stop_pct / 100) if side == "long"
                       else price * (1 + ENVELOPE.stop_pct / 100), 2)

    proposal = risk.OrderProposal(symbol, order_side, qty, price, has_stop=True, is_entry=True)
    decision = risk.check_order(proposal, ctx, ENVELOPE)
    if not decision.allowed:                       # the fuse (PRD §7.3 / V6)
        store.record_risk_event(decision.type or "rejected", {"qty": qty, "price": price}, "reject_order")
        log.warning("risk rejected entry: %s", decision.reason)
        return
    try:
        resp = ex.market_order(symbol, order_side, qty)
        ex.stop_market(symbol, stop_side, stop_price, qty)
    except ExchangeError as e:
        store.record_order(symbol, order_side, "MARKET", qty, price, "rejected", strat_name, reason)
        log.warning("entry failed: %s", e)
        return
    oid = store.record_order(symbol, order_side, "MARKET", qty, price, "filled", strat_name, reason,
                             str(resp.get("orderId")) if isinstance(resp, dict) else None)
    store.record_fill(oid, symbol, qty, price, strat_name)
    store.open_position(symbol, side, qty, price, stop_price, strat_name, reason)


def _flatten(reason: str) -> None:
    """Close the open position (reduce-only) and cancel resting orders."""
    side, pos = _current_side()
    try:
        ex.cancel_all(state.symbol)
        if pos:
            close_side = "SELL" if side == "long" else "BUY"
            ex.market_order(state.symbol, close_side, float(pos["qty"]), reduce_only=True)
    except ExchangeError as e:
        log.warning("flatten failed: %s", e)
        return
    for p in store.open_positions(state.symbol):
        store.close_position(p["id"], float(pos["upnl"]) if pos else 0.0)


def _safe_equity() -> float:
    try:
        return ex.wallet_equity_usdt()
    except ExchangeError:
        return 0.0


def run_loop() -> None:
    log.info("sunday loop start (symbol=%s tick=%ss)", state.symbol, TICK_SECONDS)
    while not state.stop.wait(0):
        try:
            tick()
        except ExchangeError as e:
            _fire(events.engine_degraded_event(str(e)))
        except Exception:
            log.exception("tick error")
        if state.stop.wait(TICK_SECONDS):
            break
    log.info("sunday loop stop")


# --- app -------------------------------------------------------------------

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
            await asyncio.to_thread(strategy.tick)
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
        with store._pool().connection() as conn:
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
