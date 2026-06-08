"""Postgres pool + redis client + a tiny migration runner + the 1.0 DAO.

Runtime state (mode/rationale/heartbeat/regime) lives in redis; the trade ledger
(strategy_state/signals/orders/positions/risk_events/webhook_log) lives in postgres.
The exchange remains the source of truth for actual positions — these tables are
our attribution/audit record (modeling-grade).
"""

from __future__ import annotations

import json
import pathlib
import time

import redis
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import settings

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"

pool: ConnectionPool | None = None
rds: redis.Redis | None = None

_HEARTBEAT_KEY = "sunday:swarm_heartbeat_ts"


def connect() -> None:
    """Open the postgres pool and redis client. Idempotent."""
    global pool, rds
    if pool is None:
        pool = ConnectionPool(settings.database_url, min_size=1, max_size=8, open=False)
        pool.open(wait=True, timeout=10)
    if rds is None:
        rds = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    rds.ping()


def close() -> None:
    global pool, rds
    if pool is not None:
        pool.close()
        pool = None
    if rds is not None:
        rds.close()
        rds = None


def run_migrations() -> list[str]:
    """Apply un-applied migrations/*.sql in filename order, once each."""
    assert pool is not None, "call connect() first"
    with pool.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    applied: list[str] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if path.name in done:
            continue
        with pool.connection() as conn:  # one transaction per migration
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
        applied.append(path.name)
    return applied


# --- runtime state (redis) -------------------------------------------------

def get_mode() -> str:
    return (rds.get("sunday:mode") if rds else None) or "active"


def set_mode(mode: str) -> None:
    if rds:
        rds.set("sunday:mode", mode)


def get_rationale() -> str | None:
    return rds.get("sunday:rationale") if rds else None


def set_rationale(text: str) -> None:
    if rds:
        rds.set("sunday:rationale", text)


def set_heartbeat() -> None:
    if rds:
        rds.set("sunday:heartbeat_ts", time.time())


def heartbeat_age() -> float | None:
    """Seconds since the last swarm heartbeat, or None if never seen."""
    v = rds.get("sunday:heartbeat_ts") if rds else None
    return (time.time() - float(v)) if v else None


def get_last_regime() -> str | None:
    return rds.get("sunday:last_regime") if rds else None


def set_last_regime(regime: str) -> None:
    if rds:
        rds.set("sunday:last_regime", regime)


def get_last_event_ts() -> str | None:
    return rds.get("sunday:last_event_ts") if rds else None


def set_last_event_ts(ts_iso: str) -> None:
    if rds:
        rds.set("sunday:last_event_ts", ts_iso)


# --- strategy state --------------------------------------------------------

def set_strategy(symbol: str, strategy: str, reason: str, set_by: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO strategy_state (symbol, strategy, reason, set_by) VALUES (%s,%s,%s,%s)",
            (symbol, strategy, reason, set_by),
        )


def current_strategy(symbol: str) -> str:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT strategy FROM strategy_state WHERE symbol=%s ORDER BY set_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    return row[0] if row else "flat"


# --- ledger ----------------------------------------------------------------

def record_signal(symbol: str, strategy: str, indicators: dict, action: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO signals (symbol, strategy, indicators_json, action) VALUES (%s,%s,%s,%s)",
            (symbol, strategy, Jsonb(indicators), action),
        )


def record_order(
    symbol: str, side: str, type_: str, qty: float, price: float | None,
    status: str, exchange_order_id: str | None, strategy: str, intent: str | None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO orders (symbol, side, type, qty, price, status, exchange_order_id, strategy, intent)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (symbol, side, type_, qty, price, status, exchange_order_id, strategy, intent),
        )


def record_position_open(
    symbol: str, side: str, qty: float, entry: float, stop: float | None,
    strategy: str, entry_reason: str | None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO positions (symbol, side, qty, entry_price, stop_price, strategy, entry_reason)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (symbol, side, qty, entry, stop, strategy, entry_reason),
        )


def close_open_positions(symbol: str, realized_pnl: float | None = None) -> None:
    """Mark open rows closed; persist realized_pnl (for per-strategy attribution).

    realized_pnl is captured by the caller from the position's unrealizedPnl the
    instant before closing — the accurate proxy ccxt gives us on testnet without
    fill-by-fill reconciliation. 1.0/1.1 hold ≤1 open row per symbol, so the
    single captured value maps cleanly onto the row.
    """
    with pool.connection() as conn:
        conn.execute(
            "UPDATE positions SET closed_at=now(), realized_pnl=COALESCE(%s, realized_pnl)"
            " WHERE symbol=%s AND closed_at IS NULL",
            (realized_pnl, symbol),
        )


def record_risk_event(type_: str, detail: dict, action_taken: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO risk_events (type, detail, action_taken) VALUES (%s,%s,%s)",
            (type_, Jsonb(detail), action_taken),
        )


def record_webhook(
    event_type: str, to_member: str, title: str | None, body: str | None,
    http_status: int | None, message_id: str | None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO webhook_log (event_type, to_member, title, body, http_status, message_id)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (event_type, to_member, title, body, http_status, message_id),
        )


# --- dashboard data (milestone 2.0) ----------------------------------------

def record_pnl_snapshot(equity: float, realized: float, unrealized: float, drawdown_pct: float | None) -> None:
    """One point on the equity curve. Written by the watcher tick (skip on exchange error)."""
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO pnl_snapshots (equity, realized, unrealized, drawdown_pct) VALUES (%s,%s,%s,%s)",
            (equity, realized, unrealized, drawdown_pct),
        )


def equity_peak() -> float | None:
    """High-water equity over all snapshots (for drawdown_pct)."""
    with pool.connection() as conn:
        row = conn.execute("SELECT MAX(equity) FROM pnl_snapshots").fetchone()
    return float(row[0]) if row and row[0] is not None else None


def realized_total(since=None) -> float:
    """Cumulative realized PnL from closed positions (optionally since a datetime)."""
    with pool.connection() as conn:
        if since is not None:
            row = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl),0) FROM positions"
                " WHERE closed_at IS NOT NULL AND closed_at >= %s",
                (since,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl),0) FROM positions WHERE closed_at IS NOT NULL"
            ).fetchone()
    return float(row[0] or 0)


def open_positions_meta_map() -> dict[str, dict]:
    """symbol -> latest open position's {strategy, entry_reason, stop_price} (for /positions join)."""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (symbol) symbol, strategy, entry_reason, stop_price"
            " FROM positions WHERE closed_at IS NULL ORDER BY symbol, opened_at DESC"
        ).fetchall()
    return {
        r[0]: {
            "strategy": r[1],
            "entry_reason": r[2],
            "stop_price": float(r[3]) if r[3] is not None else None,
        }
        for r in rows
    }


# --- commentary (analyst's User-facing market notes; harmless write) -------

def record_commentary(author: str, title: str | None, body: str) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO commentary (author, title, body) VALUES (%s,%s,%s) RETURNING id",
            (author, title, body),
        ).fetchone()
    return int(row[0])


def list_commentary(since=None, limit: int = 50) -> list[dict]:
    with pool.connection() as conn:
        if since is not None:
            rows = conn.execute(
                "SELECT ts, author, title, body FROM commentary WHERE ts >= %s ORDER BY ts DESC LIMIT %s",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts, author, title, body FROM commentary ORDER BY ts DESC LIMIT %s",
                (limit,),
            ).fetchall()
    return [{"ts": r[0].isoformat(), "author": r[1], "title": r[2], "body": r[3]} for r in rows]


# --- dashboard read aggregations (milestone 2.0 / T2) ----------------------

def equity_curve(since=None) -> list[list]:
    """[[ts_ms, equity], ...] from pnl_snapshots, oldest first."""
    with pool.connection() as conn:
        if since is not None:
            rows = conn.execute(
                "SELECT EXTRACT(EPOCH FROM ts)*1000, equity FROM pnl_snapshots"
                " WHERE ts >= %s ORDER BY ts",
                (since,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT EXTRACT(EPOCH FROM ts)*1000, equity FROM pnl_snapshots ORDER BY ts"
            ).fetchall()
    return [[int(r[0]), float(r[1])] for r in rows]


def performance(since=None) -> list[dict]:
    """Per-strategy attribution from closed positions (+ open_qty from open ones)."""
    clause = "AND closed_at >= %s" if since is not None else ""
    params = (since,) if since is not None else ()
    with pool.connection() as conn:
        closed = conn.execute(
            "SELECT strategy, COALESCE(SUM(realized_pnl),0), COUNT(*),"
            " COUNT(*) FILTER (WHERE realized_pnl > 0), AVG(realized_pnl)"
            f" FROM positions WHERE closed_at IS NOT NULL {clause} GROUP BY strategy",
            params,
        ).fetchall()
        open_rows = conn.execute(
            "SELECT strategy, COALESCE(SUM(qty),0) FROM positions"
            " WHERE closed_at IS NULL GROUP BY strategy"
        ).fetchall()
    open_qty = {r[0]: float(r[1]) for r in open_rows}
    out, seen = [], set()
    for strat, realized, n, wins, avg in closed:
        seen.add(strat)
        out.append({
            "strategy": strat,
            "realized_pnl": round(float(realized), 4),
            "n_trades": int(n),
            "win_rate": round(wins / n, 4) if n else 0.0,
            "avg_pnl": round(float(avg), 4) if avg is not None else 0.0,
            "open_qty": open_qty.get(strat, 0.0),
        })
    for strat, qty in open_qty.items():  # strategies with only open (no closed) trades
        if strat not in seen:
            out.append({"strategy": strat, "realized_pnl": 0.0, "n_trades": 0,
                        "win_rate": 0.0, "avg_pnl": 0.0, "open_qty": qty})
    return out


def strategy_history(since=None) -> list[dict]:
    """strategy_state timeline (for the equity-curve switch overlay), oldest first."""
    with pool.connection() as conn:
        if since is not None:
            rows = conn.execute(
                "SELECT EXTRACT(EPOCH FROM set_at)*1000, symbol, strategy, reason, set_by"
                " FROM strategy_state WHERE set_at >= %s ORDER BY set_at",
                (since,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT EXTRACT(EPOCH FROM set_at)*1000, symbol, strategy, reason, set_by"
                " FROM strategy_state ORDER BY set_at"
            ).fetchall()
    return [
        {"set_at_ms": int(r[0]), "symbol": r[1], "strategy": r[2], "reason": r[3], "set_by": r[4]}
        for r in rows
    ]
