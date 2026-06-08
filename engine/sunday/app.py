"""Sunday HTTP service — the full engine (milestone-1.0 + 1.1 + 1.2 basket).

This is the thin wiring layer: FastAPI routes + a background trading loop. Every
*decision* is delegated to a unit-tested pure module —

  indicators / strategy / regime  → what the tape says
  risk                            → the deterministic fuse (never the LLM)
  execution.plan_transition       → open / flip / close / hold
  views                           → the /signals panel + defensive /strategy,/envelope
  attribution                     → per-switch outcome lens (closed loop)
  events                          → self-sufficient webhooks

— so the part that can't be unit-tested without postgres/exchange (this file) stays
as mechanical as possible. The loop runs in a daemon thread (store + exchange +
urllib are all sync) and is wrapped so one bad tick degrades only that tick.

Multi-symbol (M1.2): strategy, regime, and position are **per-symbol** (strategy is
DB-keyed by symbol); mode / drawdown-lock / equity-peak / halt / heartbeat are
**account-level** (one box of risk across the whole basket). The total-exposure cap
therefore sums across symbols when sizing a new entry.
"""

from __future__ import annotations

import logging
import pathlib
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from . import attribution, events, execution, regime, risk, store, strategy, views
from .config import settings
from .exchange import BinanceUSDM, ExchangeError

log = logging.getLogger("sunday")
_MANUAL = pathlib.Path(__file__).resolve().parent / "manual.md"


def _parse_symbols(raw: str) -> list[str]:
    syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
    return syms or ["BTCUSDT"]


SYMBOLS = _parse_symbols(settings.sunday_symbols)   # the basket (M1.2)
SYMBOL = SYMBOLS[0]                                  # default for symbol-scoped endpoints
TIMEFRAME = "1h"
TICK_SECONDS = 60            # loop cadence; the strategy itself reads 1h bars
WATCHDOG_MINUTES = 90       # no swarm heartbeat for this long → safe-mode (PRD §7.6)
# The active risk envelope lives on EngineState.envelope (runtime-settable via /envelope).


@dataclass
class EngineState:
    mode: str = "flat"                       # flat | running | safe | halt (account-level)
    locked: bool = False                     # drawdown breaker latched (account-level)
    peak_equity: float = 0.0
    last_event_ts: str | None = None
    last_regime: dict = field(default_factory=dict)   # symbol → last emitted regime label
    last_candles: dict = field(default_factory=dict)  # symbol → last fetched Candles
    envelope: risk.Envelope = risk.DEFAULT_ENVELOPE   # runtime-settable via /envelope
    last_rollup_date: str | None = None               # YYYY-MM-DD of last daily_rollup_ready
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


class EnvelopeBody(BaseModel):
    reason: str | None = None
    max_position_usd: float | None = None
    max_total_exposure_usd: float | None = None
    max_leverage: float | None = None
    max_drawdown_pct: float | None = None
    stop_pct: float | None = None


class CommentaryBody(BaseModel):
    body: str
    author: str = "analyst"


class RestartBody(BaseModel):
    confirm: bool = False
    reason: str = ""


# --- helpers ---------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _heartbeat_ok() -> bool:
    last = store.last_heartbeat()
    if last is None:
        return False
    return (_now() - last).total_seconds() < WATCHDOG_MINUTES * 60


def _safe_equity() -> float:
    try:
        return ex.wallet_equity_usdt()
    except ExchangeError:
        return 0.0


def _all_positions() -> list[dict]:
    try:
        return ex.positions(None)
    except ExchangeError:
        return []


def _current_side(symbol: str) -> tuple[str | None, dict | None]:
    """Book side for one symbol from the exchange (truth), or None when flat/unreachable."""
    try:
        pos = ex.positions(symbol)
    except ExchangeError:
        return None, None
    if not pos:
        return None, None
    return pos[0]["side"], pos[0]


def _total_exposure_usd(exclude_symbol: str | None = None, positions: list[dict] | None = None) -> float:
    """Sum of |notional| across all open positions (account-level exposure). When
    sizing an entry on `exclude_symbol`, that symbol's own current exposure is left
    out so the new order's notional is added on top of the *rest* of the basket."""
    rows = positions if positions is not None else _all_positions()
    return sum(abs(float(p["qty"]) * float(p["mark"])) for p in rows if p["symbol"] != exclude_symbol)


def _symbol_status(symbol: str) -> dict:
    strat_name = store.current_strategy(symbol)
    candles = state.last_candles.get(symbol)
    rationale = None
    out_votes = None
    if candles is not None:
        try:
            rationale = strategy.evaluate(strat_name, candles).rationale
        except ValueError:
            pass
        out_votes = views.votes_summary(candles)
    _, pos = _current_side(symbol)
    out = {"symbol": symbol, "strategy": strat_name, "strategy_rationale": rationale, "position": pos}
    if out_votes is not None:
        out["votes"] = out_votes
    return out


def _gather_status() -> dict:
    """Account-level snapshot + a per-symbol summary list (the /status shape and the
    self-sufficient body of every webhook)."""
    equity = _safe_equity()
    positions = _all_positions()
    exposure = _total_exposure_usd(positions=positions)
    return {
        "alive": True,
        "mode": state.mode,
        "as_of_ts": _now().isoformat(),
        "equity": equity,
        "exposure_usd": exposure,
        "leverage": (exposure / equity) if equity else 0.0,
        "pnl_day": sum(float(p["upnl"]) for p in positions),
        "drawdown_pct": risk.drawdown_pct(equity, state.peak_equity),
        "last_event_ts": state.last_event_ts,
        "swarm_heartbeat_ok": _heartbeat_ok(),
        "last_lever": store.last_lever(),       # latest switch across the basket
        "envelope": state.envelope.as_dict(),
        "symbols": [_symbol_status(s) for s in SYMBOLS],
    }


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

def _maybe_daily_rollup(now: datetime) -> None:
    """Fire daily_rollup_ready once per day after the review hour — the reviewer's
    event-driven alternative to its 17:00 timer."""
    today = now.date().isoformat()
    if now.hour >= 17 and state.last_rollup_date != today:
        state.last_rollup_date = today
        _fire(events.build_event("daily_rollup_ready", title="Daily rollup ready",
                                 body="當日資料齊備，可復盤。", status=_gather_status(), to="reviewer"))


def tick() -> None:
    """One engine cycle. Account-level guards run once; each symbol is then read +
    reconciled. Wrapped by run_loop so a raised error degrades one tick."""
    now = _now()

    # 1) liveness: no swarm heartbeat → safe-mode floor (account-level, PRD §7.6)
    if not _heartbeat_ok() and state.mode not in ("safe", "halt"):
        state.mode = "safe"
        _fire(events.build_event("safe_mode_entered", title="Safe-mode entered",
                                 body="swarm heartbeat 逾時，Sunday 凍結新倉（既有倉留 stop）。",
                                 status=_gather_status(), to="leader"))

    # 2) drawdown breaker (deterministic, non-LLM, account-level)
    try:
        equity = ex.wallet_equity_usdt()
        state.peak_equity = max(state.peak_equity, equity)
        dd = risk.check_drawdown(equity, state.peak_equity, state.envelope)
        if dd.breached and not state.locked:
            state.locked = True
            _flatten_all(reason="drawdown breaker")
            store.record_risk_event("drawdown", {"drawdown_pct": dd.drawdown_pct}, "flatten_and_lock")
            _fire(events.build_event("risk_breach", title="Risk breach: drawdown",
                                     body=dd.reason, status=_gather_status(), to="leader"))
    except ExchangeError:
        pass

    _maybe_daily_rollup(now)

    if state.mode == "flat" and not state.locked:   # promote past the cold-start gate
        state.mode = "running"
    trading = state.mode == "running" and not state.locked

    # 3) per-symbol: regime read (event-gated webhook) + reconcile
    for symbol in SYMBOLS:
        try:
            candles = ex.fetch_klines(symbol, TIMEFRAME, 200)
            state.last_candles[symbol] = candles
            rr = regime.classify(candles)
            prev = state.last_regime.get(symbol)
            if regime.is_shift(prev, rr.label):
                _fire(events.regime_shift_event(symbol, prev, rr, _gather_status()))
            if rr.label != "unknown":
                state.last_regime[symbol] = rr.label
            if trading:
                _reconcile(symbol, candles)
        except ExchangeError as e:
            _fire(events.engine_degraded_event(f"{symbol}: {e}"))


def _reconcile(symbol: str, candles) -> None:
    """Bring one symbol's book in line with its active strategy's target, risk-gated."""
    strat_name = store.current_strategy(symbol)
    target = strategy.target_side(strat_name, candles)
    side, _ = _current_side(symbol)
    action = execution.plan_transition(side, target)
    if action == execution.HOLD:
        return

    vote = strategy.evaluate(strat_name, candles) if strat_name != "flat" else None
    store.record_signal(symbol, strat_name, vote.indicators if vote else {}, action)
    price = candles.last_close or 0.0

    if action == execution.CLOSE or action.startswith("flip"):
        _flatten(symbol, reason=f"{action} ({strat_name})")
        if action == execution.CLOSE:
            return

    want = "long" if action in (execution.OPEN_LONG, execution.FLIP_LONG) else "short"
    _open(symbol, want, price, strat_name, vote.rationale if vote else "")


def _open(symbol: str, side: str, price: float, strat_name: str, reason: str) -> None:
    """Size within the envelope (exposure summed across the basket), gate, then place
    a market entry + an exchange-native stop."""
    ctx = risk.RiskContext(equity=_safe_equity(), current_exposure_usd=_total_exposure_usd(exclude_symbol=symbol))
    qty = round(risk.max_allowed_qty(price, ctx, state.envelope), 3)
    if qty <= 0:
        return
    order_side = "BUY" if side == "long" else "SELL"
    stop_side = "SELL" if side == "long" else "BUY"
    stop_price = round(price * (1 - state.envelope.stop_pct / 100) if side == "long"
                       else price * (1 + state.envelope.stop_pct / 100), 2)

    proposal = risk.OrderProposal(symbol, order_side, qty, price, has_stop=True, is_entry=True)
    decision = risk.check_order(proposal, ctx, state.envelope)
    if not decision.allowed:                       # the fuse (PRD §7.3 / V6)
        store.record_risk_event(decision.type or "rejected", {"symbol": symbol, "qty": qty, "price": price}, "reject_order")
        log.warning("risk rejected entry on %s: %s", symbol, decision.reason)
        return
    try:
        resp = ex.market_order(symbol, order_side, qty)
        ex.stop_market(symbol, stop_side, stop_price, qty)
    except ExchangeError as e:
        store.record_order(symbol, order_side, "MARKET", qty, price, "rejected", strat_name, reason)
        log.warning("entry failed on %s: %s", symbol, e)
        return
    oid = store.record_order(symbol, order_side, "MARKET", qty, price, "filled", strat_name, reason,
                             str(resp.get("orderId")) if isinstance(resp, dict) else None)
    store.record_fill(oid, symbol, qty, price, strat_name)
    store.open_position(symbol, side, qty, price, stop_price, strat_name, reason)


def _flatten(symbol: str, reason: str) -> None:
    """Close one symbol's open position (reduce-only) and cancel its resting orders."""
    side, pos = _current_side(symbol)
    try:
        ex.cancel_all(symbol)
        if pos:
            close_side = "SELL" if side == "long" else "BUY"
            ex.market_order(symbol, close_side, float(pos["qty"]), reduce_only=True)
    except ExchangeError as e:
        log.warning("flatten %s failed: %s", symbol, e)
        return
    for p in store.open_positions(symbol):
        store.close_position(p["id"], float(pos["upnl"]) if pos else 0.0)


def _flatten_all(reason: str) -> None:
    for symbol in SYMBOLS:
        _flatten(symbol, reason)


def run_loop() -> None:
    log.info("sunday loop start (symbols=%s tick=%ss)", ",".join(SYMBOLS), TICK_SECONDS)
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
    env = store.get_envelope()      # restore the leader's envelope across restarts
    if env:
        state.envelope = risk.Envelope(**env)
    state.stop.clear()
    thread = threading.Thread(target=run_loop, name="sunday-loop", daemon=True)
    thread.start()
    yield
    state.stop.set()
    thread.join(timeout=5)
    store.close()


app = FastAPI(title="Sunday", version="0.3.0", lifespan=lifespan)


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
    rows = _all_positions()
    # enrich with the engine's entry_reason/strategy/stop from the ledger
    open_rows = {p["symbol"]: p for p in store.open_positions(None)}
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
    unrealized = sum(float(p["upnl"]) for p in _all_positions())
    return {
        "realized": None,  # realized series lives in pnl_snapshots; equity_curve carries it
        "unrealized": unrealized,
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
            _flatten(body.symbol, reason="strategy→flat")
    return JSONResponse(resp, status_code=code)


@app.post("/halt")
def post_halt(body: HaltBody) -> dict:
    state.mode = "halt" if body.mode == "flat" else "safe"
    if body.mode == "flat":
        _flatten_all(reason=f"halt: {body.reason}")
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


@app.post("/envelope")
def post_envelope(body: EnvelopeBody) -> Response:
    updates = {k: v for k, v in body.model_dump().items()
               if k in risk.Envelope.FIELDS and v is not None}
    resp, code = views.apply_envelope(state.envelope.as_dict(), updates, body.reason)
    if code == 200 and resp.get("applied"):
        new_env = resp["resulting_status"]["envelope"]
        state.envelope = risk.Envelope(**new_env)
        store.set_envelope(new_env, body.reason or "", "friday")
    return JSONResponse(resp, status_code=code)


@app.post("/commentary")
def post_commentary(body: CommentaryBody) -> dict:
    # analyst's one harmless, User-facing write (PRD §7.11) — not a trading lever.
    store.record_commentary(body.author, body.body)
    return {"ok": True}


@app.get("/commentary")
def get_commentary(since: str | None = None) -> dict:
    since_dt = datetime.fromisoformat(since) if since else None
    return {"commentary": store.list_commentary(since_dt)}


@app.post("/restart")
def post_restart(body: RestartBody) -> Response:
    if not body.confirm:
        return JSONResponse({"ok": False, "error": "confirm_required",
                             "message": "restart is non-idempotent — pass confirm=true"}, status_code=400)
    # Reset supervision state + force a re-sync next tick (peak re-seeds from live equity).
    state.locked = False
    state.mode = "running"
    state.last_regime = {}
    state.peak_equity = _safe_equity()
    store.record_risk_event("restart", {"reason": body.reason}, "engine state reset + re-sync")
    return JSONResponse({"ok": True, "resulting_status": {"mode": state.mode, "locked": state.locked}})


@app.get("/trades")
def get_trades(since: str | None = None) -> dict:
    since_dt = datetime.fromisoformat(since) if since else None
    return {"trades": store.list_trades(since_dt)}
