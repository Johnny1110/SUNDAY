"""Sunday HTTP service — the execution/risk/information substrate's HTTP boundary.

Thin FastAPI layer: the trading logic lives in engine.py (over ports), the pure
HTTP logic in views.py, the info/desk/ablation chains in feeds/desk/ablation. This
file only wires endpoints to those + the background watcher.

- read panels (auto-allow for the swarm): /status /desk /advisor /market /positions
  /pnl /performance /risk /thesis /theses /ablation /trades /events /commentary
- levers (permission-gated, leader): /thesis (directed execution) /strategy /halt
  /envelope /heartbeat
- self-served User dashboard at /dashboard (Vue 3, assets under /ui)
- background watcher (_tick_once): ingest info-layer feeds → engine self-check
  (regime/watchdog/equity) → desk notable-score wake → ablation shadow snapshot
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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ablation, advisor, desk, engine, exchange, feeds, risk, store, views
from .config import settings
from .market import Candles

log = logging.getLogger("sunday")
_HERE = pathlib.Path(__file__).resolve().parent
_MANUAL = _HERE / "manual.md"
_WEB = _HERE / "web"            # the Sunday-served User dashboard (Vue 3, no build step)
_INDEX = _WEB / "index.html"

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


def _tick_once() -> None:
    """One periodic cycle: ingest info-layer feeds (T1) → engine self-check (regime/
    watchdog/equity) → desk notable-score wake (T2). Runs in a worker thread."""
    feeds.ingest_all(settings.symbol_list)
    engine.tick()
    desk.check_notable_and_notify(settings.symbol_list)
    ablation.snapshot_shadows(settings.symbol_list)


async def _watch_loop() -> None:
    """Periodic cheap self-check: feeds + regime detection (-> webhook) + dead-man watchdog."""
    while True:
        try:
            await asyncio.sleep(settings.tick_interval_sec)
            await asyncio.to_thread(_tick_once)
        except asyncio.CancelledError:
            break
        except Exception as e:  # never let the watcher die
            log.warning("watcher tick error: %s", e)


app = FastAPI(title="Sunday", version="0.1.0", lifespan=lifespan)

# Serve the User dashboard's static assets (vendored Vue + lightweight-charts + our
# ES modules). D12 / invariant 9: the dashboard is Sunday-served, never in evva.
if _WEB.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_WEB)), name="ui")


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


def _active_envelope() -> dict:
    """The active risk caps (leader's latest /envelope), or the config defaults if unset."""
    return store.current_envelope() or {
        "max_position_usd": settings.max_position_usd,
        "max_total_exposure_usd": settings.max_total_exposure_usd,
        "max_leverage": float(settings.max_leverage),
        "max_drawdown_pct": settings.max_drawdown_pct,
        "stop_pct": settings.stop_pct,
    }


class StrategyReq(BaseModel):
    symbol: str = "BTCUSDT"
    strategy: str
    reason: str
    expected_current: str | None = None  # optimistic concurrency: stale view → 409 (PRD §7.10)
    set_by: str = "leader"  # agents default to 'leader'; the User dashboard sends 'user'


class HaltReq(BaseModel):
    reason: str
    mode: str = "flat"  # flat | safe
    set_by: str = "system"


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
    set_by: str = "leader"


class ThesisReq(BaseModel):
    symbol: str = "BTCUSDT"
    direction: str                       # long | short | flat
    conviction: float                    # 0..1 → size as a fraction of the position cap
    rationale: str
    horizon: str | None = None
    invalidation: str | None = None
    invalidation_price: float | None = None
    evidence: dict | None = None
    set_by: str = "friday"


@app.get("/manual", response_class=PlainTextResponse)
def manual() -> str:
    return _MANUAL.read_text()


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    """Sunday-served User dashboard (Vue 3 SPA shell; assets under /ui). D12: not in evva."""
    return _INDEX.read_text()


@app.get("/status")
def status() -> dict:
    """Posture across the whole basket (milestone-4). Account-level (mode / equity /
    aggregated exposure / heartbeat) + a per-symbol `basket` (strategy + active thesis +
    live position). Top-level symbol/strategy/position mirror the primary symbol for
    back-compat. Exposure is summed over EVERY position, not just the primary."""
    mode = store.get_mode()
    rationale = store.get_rationale() or "(尚無決策)"
    age = store.heartbeat_age()
    swarm_ok = age is None or age < settings.heartbeat_timeout_sec
    symbols = settings.symbol_list
    strat_by = {s: store.current_strategy(s) for s in symbols}
    thesis_by = {s: store.current_thesis(s) for s in symbols}

    pos_by_unified: dict[str, dict] = {}
    exposure = equity = 0.0
    try:
        for p in exchange.fetch_positions():           # exchange = source of truth for the book
            notional = abs(float(p.get("contracts") or 0) * float(p.get("markPrice") or 0))
            exposure += notional                       # FIX: aggregate the WHOLE basket, not just primary
            pos_by_unified[p.get("symbol")] = {
                "side": p.get("side"), "qty": p.get("contracts"), "entry": p.get("entryPrice"),
                "mark": p.get("markPrice"), "upnl": p.get("unrealizedPnl"),
            }
        bal = exchange.fetch_balance()
        equity = float((bal.get("total") or {}).get("USDT") or 0.0)
    except Exception as e:
        rationale = f"{rationale} [status: exchange unreachable: {type(e).__name__}]"

    def _pos(sym: str) -> dict | None:
        return pos_by_unified.get(exchange.to_unified(sym))

    basket = []
    for s in symbols:
        t = thesis_by.get(s)
        basket.append({
            "symbol": s,
            "strategy": strat_by.get(s),
            "thesis": ({"id": t["id"], "direction": t["direction"], "conviction": t["conviction"]}
                       if t else None),
            "position": _pos(s),
        })
    primary = settings.symbol
    return {
        "alive": True,
        "mode": mode,
        "symbol": primary,                       # back-compat: the primary/default symbol
        "strategy": strat_by.get(primary),       # back-compat: primary symbol's strategy
        "strategy_rationale": rationale,
        "position": _pos(primary),               # back-compat: primary symbol's position
        "basket": basket,                        # milestone-4: per-symbol posture across the basket
        "exposure_usd": exposure,                # aggregated across the whole basket
        "leverage": round(exposure / equity, 3) if equity > 0 else 0.0,  # effective (= /risk's definition)
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


@app.get("/desk")
def desk_panel(symbol: str | None = None) -> dict:
    """Research desk's 'where to look' (milestone-4 T2). No symbol → basket panel
    (cheap, from stored metrics, most-notable first). With symbol → deep view:
    metrics trend + regime/advisor (fetches candles for that one symbol)."""
    metrics_map = store.latest_perp_metrics_all()
    if symbol:
        recent = store.perp_metrics_recent(symbol, 30)
        oi_chg = desk.oi_change_pct(recent)
        adv: dict = {}
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, settings.timeframe, 200)
            funding = exchange.fetch_funding_rate(symbol)
            adv = advisor.advise(Candles.from_klines(ohlcv), funding, store.current_strategy(symbol), symbol=symbol)
        except Exception as e:
            adv = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        vol = (adv.get("regime") or {}).get("vol_pct")
        return {
            "symbol": symbol,
            "info_mode": "off" if symbol in settings.info_off_list else "on",
            "summary": desk.symbol_summary(symbol, metrics_map.get(symbol), oi_chg, vol),
            "metrics_recent": recent[:10],
            "advisor": adv,
        }
    recent_map = {s: store.perp_metrics_recent(s, 30) for s in settings.symbol_list}
    return {"basket": desk.build_basket(settings.symbol_list, metrics_map, recent_map, settings.info_off_list)}


@app.get("/positions")
def positions() -> list[dict]:
    _require_key()
    raw = _ex(lambda: exchange.fetch_positions())
    metas = store.open_positions_meta_map()  # DB row gives strategy/entry_reason/stop
    out = []
    for p in raw:
        meta: dict = {}
        for db_sym, m in metas.items():
            if exchange.to_unified(db_sym) == p.get("symbol"):
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


@app.get("/thesis")
def get_thesis(symbol: str = "BTCUSDT") -> dict:
    """The symbol's current active thesis (drives `directed` execution), or null."""
    t = store.current_thesis(symbol)
    return t or {"symbol": symbol, "active": None}


@app.get("/theses")
def get_theses(since: str | None = None, limit: int = 100) -> list[dict]:
    """Thesis history + outcomes (closed-loop attribution / ablation)."""
    return store.list_theses(_parse_since(since), min(limit, 500))


@app.post("/strategy")
def post_strategy(req: StrategyReq) -> dict:
    """Strategy lever (leader). Routed through views.apply_strategy: strategy must be
    valid, reason is mandatory (§7.11), and an optional expected_current gives the agent
    optimistic concurrency (stale view → 409, not a silent mis-set). Reconciles on success."""
    current = store.current_strategy(req.symbol)
    body, code = views.apply_strategy(current, req.strategy, req.reason, req.expected_current, req.symbol)
    if code != 200:
        raise HTTPException(code, body["message"])
    _require_key()
    if body["applied"]:  # idempotent: a no-op switch writes no duplicate strategy_state row
        store.set_strategy(req.symbol, req.strategy, req.reason, set_by=req.set_by)
    store.set_mode("active")
    try:
        result = engine.reconcile(req.symbol, set_by=req.set_by)
    except risk.RiskRejected as e:
        raise HTTPException(409, f"risk rejected: {e}")
    except Exception as e:
        raise HTTPException(502, f"exchange error: {type(e).__name__}: {str(e)[:300]}")
    return {
        "ok": True,
        "symbol": req.symbol,
        "strategy": req.strategy,
        "applied": body["applied"],
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }


@app.post("/thesis")
def post_thesis(req: ThesisReq) -> dict:
    """Thesis lever (milestone-4): the desk's structured view drives `directed` execution.
    Validates input, supersedes the symbol's active thesis, switches to `directed` if
    needed, and reconciles. The deterministic risk fuses still gate the resulting order
    (a too-large/over-exposed thesis is rejected, not silently shrunk → 409)."""
    body, code = views.validate_thesis(req.direction, req.conviction, req.rationale)
    if code != 200:
        raise HTTPException(code, body["message"])
    _require_key()
    tid = store.set_thesis(req.symbol, req.direction, req.conviction, req.rationale, req.set_by,
                           horizon=req.horizon, invalidation=req.invalidation,
                           invalidation_price=req.invalidation_price, evidence=req.evidence)
    if store.current_strategy(req.symbol) != "directed":
        store.set_strategy(req.symbol, "directed", f"thesis {tid}: {req.rationale}", req.set_by)
    store.set_mode("active")
    try:
        result = engine.reconcile(req.symbol, set_by=req.set_by)
    except risk.RiskRejected as e:
        raise HTTPException(409, f"risk rejected: {e}")
    except Exception as e:
        raise HTTPException(502, f"exchange error: {type(e).__name__}: {str(e)[:300]}")
    return {
        "ok": True, "thesis_id": tid, "symbol": req.symbol, "direction": req.direction,
        "conviction": req.conviction, "applied_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }


@app.post("/halt")
def post_halt(req: HaltReq) -> dict:
    if req.mode not in ("flat", "safe"):
        raise HTTPException(400, "mode must be 'flat' or 'safe'")
    _require_key()
    try:
        result = engine.halt(req.mode, req.reason, set_by=req.set_by)
    except Exception as e:
        raise HTTPException(502, f"exchange error: {type(e).__name__}: {str(e)[:300]}")
    return {"ok": True, **result}


@app.get("/envelope")
def get_envelope() -> dict:
    """The active risk envelope (latest set), or the configured defaults if none set yet."""
    return _active_envelope()


@app.get("/risk")
def risk_panel() -> dict:
    """Risk panel: active caps vs a best-effort live read + per-cap utilization +
    current violations + the recent deterministic-fuse log (risk_events).

    Caps + events are pure DB (always render); equity/exposure/drawdown are live
    extras that degrade to the last pnl snapshot when the exchange is unreachable.
    """
    env = _active_envelope()
    equity = exposure = position_usd = 0.0
    try:
        for p in exchange.fetch_positions():
            notional = abs(float(p.get("contracts") or 0) * float(p.get("markPrice") or 0))
            exposure += notional
            position_usd = max(position_usd, notional)
        bal = exchange.fetch_balance()
        equity = float((bal.get("total") or {}).get("USDT") or 0.0)
    except Exception:
        pass
    peak = store.equity_peak()
    if equity > 0 and peak:
        drawdown = risk.drawdown_pct(equity, max(peak, equity))
    else:  # exchange unreachable — fall back to the last recorded snapshot
        snap = store.latest_pnl_snapshot()
        if snap:
            equity = equity or snap["equity"]
            drawdown = snap.get("drawdown_pct") or 0.0
        else:
            drawdown = 0.0
    leverage = (exposure / equity) if equity > 0 else 0.0
    current = {"equity": equity, "position_usd": position_usd, "exposure_usd": exposure,
               "leverage": leverage, "drawdown_pct": drawdown}
    return views.risk_view(env, current, store.recent_risk_events(20))


@app.get("/ablation")
def ablation_report(since: str | None = None) -> dict:
    """The kill-line (milestone-4 M4-D5): desk vs no-trade shadow baselines +
    info-ON/OFF realized split + thesis summary. Did the information layer add value?"""
    since_dt = _parse_since(since)
    return ablation.build_report(
        store.equity_curve(since_dt), store.realized_total(since_dt),
        {b: store.shadow_curve(b, since_dt) for b in ("buy_hold", "funding_carry")},
        store.realized_by_symbol(since_dt), store.list_theses(since_dt, 500),
        settings.info_off_list,
    )


@app.get("/trades")
def trades(since: str | None = None, limit: int = 100) -> list[dict]:
    """Order ledger / blotter (PRD §7.4). Pure DB read of the orders table."""
    return store.recent_orders(_parse_since(since), min(limit, 500))


@app.get("/events")
def events(since: str | None = None, limit: int = 100) -> list[dict]:
    """Outbound wake events Sunday sent the swarm (webhook_log) — the 'what woke the
    agents' feed for the reports timeline. Pure DB read."""
    return store.recent_webhooks(_parse_since(since), min(limit, 500))


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
                       req.max_drawdown_pct, req.stop_pct, req.reason, req.set_by)
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
