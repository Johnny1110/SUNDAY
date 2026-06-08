"""Sunday HTTP service — the full engine (milestone-1.0 + milestone-3 folded in).

This is the thin wiring layer: FastAPI routes + a background trading loop. Every
*decision* is delegated to a unit-tested pure module —

  indicators / strategy / regime  → what the tape says
  risk                            → the deterministic fuse (never the LLM)
  execution.plan_transition       → open / flip / close / hold
  views                           → the /signals panel, enhanced /status, and the
                                     defensive /strategy state machine (M3)
  attribution                     → per-switch outcome lens (closed loop, M3)
  events                          → self-sufficient webhooks (M3)

— so the part that can't be unit-tested without postgres/exchange (this file) stays
as mechanical as possible. The loop runs in a daemon thread (store + exchange +
urllib are all sync) and is wrapped so one bad tick degrades only that tick.
"""

from __future__ import annotations

import logging
import pathlib
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from . import attribution, events, execution, regime, risk, store, strategy, views
from .config import settings
from .exchange import BinanceUSDM, ExchangeError
from .market import Candles

log = logging.getLogger("sunday")
_MANUAL = pathlib.Path(__file__).resolve().parent / "manual.md"

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
    state.stop.clear()
    thread = threading.Thread(target=run_loop, name="sunday-loop", daemon=True)
    thread.start()
    yield
    state.stop.set()
    thread.join(timeout=5)
    store.close()


app = FastAPI(title="Sunday", version="0.2.0", lifespan=lifespan)


@app.get("/manual", response_class=PlainTextResponse)
def manual() -> str:
    return _MANUAL.read_text()


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


@app.get("/status")
def status() -> dict:
    return _gather_status()


@app.get("/signals")
def signals(symbol: str = SYMBOL) -> dict:
    candles = ex.fetch_klines(symbol, TIMEFRAME, 200)
    return views.signals_view(symbol, candles, store.current_strategy(symbol))


@app.get("/market")
def market(symbol: str = SYMBOL, tf: str = TIMEFRAME, limit: int = 100) -> dict:
    candles = ex.fetch_klines(symbol, tf, limit)
    return {"symbol": symbol, "tf": tf, "ohlcv": candles.to_rows()}


@app.get("/positions")
def positions() -> list[dict]:
    rows = ex.positions(state.symbol)
    # enrich with the engine's entry_reason/strategy from the ledger
    open_rows = {p["symbol"]: p for p in store.open_positions(state.symbol)}
    for r in rows:
        led = open_rows.get(r["symbol"])
        if led:
            r["strategy"] = led["strategy"]
            r["entry_reason"] = led["entry_reason"]
            r["stop"] = float(led["stop_price"]) if led["stop_price"] is not None else None
    return rows


@app.get("/pnl")
def pnl(since: str | None = None) -> dict:
    since_dt = datetime.fromisoformat(since) if since else None
    _, pos = _current_side()
    return {
        "realized": None,  # realized series lives in pnl_snapshots; equity_curve carries it
        "unrealized": float(pos["upnl"]) if pos else 0.0,
        "equity": _safe_equity(),
        "equity_curve": store.equity_curve(since_dt),
    }


@app.post("/strategy")
def post_strategy(body: StrategyBody) -> Response:
    current = store.current_strategy(body.symbol)
    resp, code = views.apply_strategy(current, body.strategy, body.reason, body.expected_current, body.symbol)
    if code == 200 and resp.get("applied"):
        store.set_strategy(body.symbol, body.strategy, body.reason or "", "friday")
        # the next tick repositions to the new strategy; flat closes immediately
        if body.strategy == "flat":
            _flatten(reason="strategy→flat")
    return JSONResponse(resp, status_code=code)


@app.post("/halt")
def post_halt(body: HaltBody) -> dict:
    state.mode = "halt" if body.mode == "flat" else "safe"
    if body.mode == "flat":
        _flatten(reason=f"halt: {body.reason}")
    store.record_risk_event("halt", {"mode": body.mode, "reason": body.reason}, f"mode={state.mode}")
    return {"ok": True, "resulting_status": {"mode": state.mode}}


@app.post("/heartbeat")
def post_heartbeat() -> dict:
    ts = store.set_heartbeat()
    if state.mode == "safe" and not state.locked:    # heartbeat back → leave safe floor
        state.mode = "running"
    return {"ok": True, "watchdog_reset_at": ts}


@app.get("/strategy/outcomes")
def strategy_outcomes(symbol: str = SYMBOL, since: str | None = None) -> dict:
    since_dt = datetime.fromisoformat(since) if since else None
    switches = store.strategy_switches(symbol, since_dt)
    positions_ = store.positions_for_attribution(symbol, since_dt)
    episodes = attribution.attribute(switches, positions_)
    return {"symbol": symbol, "episodes": [e.as_dict() for e in episodes]}
