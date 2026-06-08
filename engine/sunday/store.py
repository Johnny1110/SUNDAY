"""Postgres pool + redis client + the ledger DAO.

The store is deliberately *thin* I/O: it persists/loads rows, but every decision
(strategy votes, risk, attribution) lives in the pure modules that are unit-tested
without a database. The DAO methods here are integration-tested in the deployment
environment (they need a live postgres); they are kept small and parameterised so
that surface stays low-risk.

Datastore boundary (PRD §7.8): agents never read this DB — they go through the
HTTP API; Sunday never touches the swarm's .vero. Money/qty are NUMERIC in SQL and
surface as float here (Gate-1 testnet scale; exact-decimal accounting is a Gate-2
concern if it ever matters).
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import redis
from psycopg.rows import dict_row
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


def _pool() -> ConnectionPool:
    assert pool is not None, "call connect() first"
    return pool


# --- strategy state (the lever audit + current active strategy) ------------

def current_strategy(symbol: str) -> str:
    """Latest active strategy for a symbol; 'flat' when never set."""
    with _pool().connection() as c:
        r = c.execute(
            "SELECT strategy FROM strategy_state WHERE symbol=%s ORDER BY set_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return r[0] if r else "flat"


def set_strategy(symbol: str, strategy: str, reason: str, set_by: str) -> None:
    """Append a strategy_state row — the lever's durable, User-visible record."""
    with _pool().connection() as c:
        c.execute(
            "INSERT INTO strategy_state (symbol, strategy, reason, set_by) VALUES (%s,%s,%s,%s)",
            (symbol, strategy, reason, set_by),
        )


def last_lever(symbol: str) -> dict | None:
    """The most recent strategy switch, for /status.last_lever (staleness aid)."""
    with _pool().connection() as c:
        r = c.execute(
            "SELECT set_by, strategy, set_at FROM strategy_state WHERE symbol=%s ORDER BY set_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    if not r:
        return None
    return {"by": r[0], "what": f"strategy={r[1]}", "at": r[2].isoformat()}


def strategy_switches(symbol: str, since: datetime | None = None) -> list[dict]:
    """strategy_state rows (oldest-first) for attribution.attribute()."""
    with _pool().connection() as c:
        cur = c.cursor(row_factory=dict_row)
        if since:
            cur.execute(
                "SELECT symbol, strategy, reason, set_by, set_at FROM strategy_state "
                "WHERE symbol=%s AND set_at >= %s ORDER BY set_at",
                (symbol, since),
            )
        else:
            cur.execute(
                "SELECT symbol, strategy, reason, set_by, set_at FROM strategy_state "
                "WHERE symbol=%s ORDER BY set_at",
                (symbol,),
            )
        return cur.fetchall()


# --- signals / orders / fills / positions / pnl ----------------------------

def record_signal(symbol: str, strategy: str, indicators: dict, action: str) -> None:
    with _pool().connection() as c:
        c.execute(
            "INSERT INTO signals (symbol, strategy, indicators_json, action) VALUES (%s,%s,%s,%s)",
            (symbol, strategy, json.dumps(indicators), action),
        )


def record_order(symbol: str, side: str, type_: str, qty: float, price: float | None,
                 status: str, strategy: str, intent: str, exchange_order_id: str | None = None) -> int:
    with _pool().connection() as c:
        r = c.execute(
            "INSERT INTO orders (symbol, side, type, qty, price, status, strategy, intent, exchange_order_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (symbol, side, type_, qty, price, status, strategy, intent, exchange_order_id),
        ).fetchone()
        return r[0]


def record_fill(order_id: int, symbol: str, qty: float, price: float, strategy: str, fee: float = 0.0) -> None:
    with _pool().connection() as c:
        c.execute(
            "INSERT INTO fills (order_id, symbol, qty, price, fee, strategy) VALUES (%s,%s,%s,%s,%s,%s)",
            (order_id, symbol, qty, price, fee, strategy),
        )


def open_position(symbol: str, side: str, qty: float, entry_price: float, stop_price: float | None,
                  strategy: str, entry_reason: str) -> int:
    with _pool().connection() as c:
        r = c.execute(
            "INSERT INTO positions (symbol, side, qty, entry_price, stop_price, strategy, entry_reason) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (symbol, side, qty, entry_price, stop_price, strategy, entry_reason),
        ).fetchone()
        return r[0]


def close_position(position_id: int, realized_pnl: float) -> None:
    with _pool().connection() as c:
        c.execute(
            "UPDATE positions SET closed_at=now(), realized_pnl=%s WHERE id=%s AND closed_at IS NULL",
            (realized_pnl, position_id),
        )


def open_positions(symbol: str | None = None) -> list[dict]:
    with _pool().connection() as c:
        cur = c.cursor(row_factory=dict_row)
        if symbol:
            cur.execute("SELECT * FROM positions WHERE closed_at IS NULL AND symbol=%s", (symbol,))
        else:
            cur.execute("SELECT * FROM positions WHERE closed_at IS NULL")
        return cur.fetchall()


def positions_for_attribution(symbol: str, since: datetime | None = None) -> list[dict]:
    """All positions (open + closed) for attribution.attribute()."""
    with _pool().connection() as c:
        cur = c.cursor(row_factory=dict_row)
        if since:
            cur.execute(
                "SELECT symbol, strategy, qty, entry_price, realized_pnl, opened_at, closed_at "
                "FROM positions WHERE symbol=%s AND opened_at >= %s ORDER BY opened_at",
                (symbol, since),
            )
        else:
            cur.execute(
                "SELECT symbol, strategy, qty, entry_price, realized_pnl, opened_at, closed_at "
                "FROM positions WHERE symbol=%s ORDER BY opened_at",
                (symbol,),
            )
        return cur.fetchall()


def record_pnl_snapshot(equity: float, realized: float, unrealized: float, drawdown_pct: float | None) -> None:
    with _pool().connection() as c:
        c.execute(
            "INSERT INTO pnl_snapshots (equity, realized, unrealized, drawdown_pct) VALUES (%s,%s,%s,%s)",
            (equity, realized, unrealized, drawdown_pct),
        )


def equity_curve(since: datetime | None = None) -> list[list]:
    with _pool().connection() as c:
        if since:
            rows = c.execute("SELECT ts, equity FROM pnl_snapshots WHERE ts >= %s ORDER BY ts", (since,)).fetchall()
        else:
            rows = c.execute("SELECT ts, equity FROM pnl_snapshots ORDER BY ts").fetchall()
    return [[r[0].isoformat(), float(r[1])] for r in rows]


# --- audit: risk events + webhook log --------------------------------------

def record_risk_event(type_: str, detail: dict, action_taken: str) -> None:
    with _pool().connection() as c:
        c.execute(
            "INSERT INTO risk_events (type, detail, action_taken) VALUES (%s,%s,%s)",
            (type_, json.dumps(detail), action_taken),
        )


def record_webhook(event_type: str, to_member: str, title: str, body: str,
                   http_status: int | None, message_id: str | None) -> None:
    with _pool().connection() as c:
        c.execute(
            "INSERT INTO webhook_log (event_type, to_member, title, body, http_status, message_id) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (event_type, to_member, title, body, http_status, message_id),
        )


# --- risk envelope (the leader's /envelope lever) --------------------------

def get_envelope() -> dict | None:
    """Latest risk envelope as a dict (the 5 numeric fields), or None if never set."""
    with _pool().connection() as c:
        cur = c.cursor(row_factory=dict_row)
        cur.execute(
            "SELECT max_position_usd, max_total_exposure_usd, max_leverage, max_drawdown_pct, stop_pct "
            "FROM risk_envelope ORDER BY set_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    return {k: float(v) for k, v in row.items()} if row else None


def set_envelope(env: dict, reason: str, set_by: str) -> None:
    with _pool().connection() as c:
        c.execute(
            "INSERT INTO risk_envelope (max_position_usd, max_total_exposure_usd, max_leverage, "
            "max_drawdown_pct, stop_pct, reason, set_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (env["max_position_usd"], env["max_total_exposure_usd"], env["max_leverage"],
             env["max_drawdown_pct"], env["stop_pct"], reason, set_by),
        )


# --- analyst commentary (User-facing feed) + trades read -------------------

def record_commentary(author: str, body: str) -> None:
    with _pool().connection() as c:
        c.execute("INSERT INTO commentary (author, body) VALUES (%s,%s)", (author, body))


def list_commentary(since: datetime | None = None, limit: int = 50) -> list[dict]:
    with _pool().connection() as c:
        cur = c.cursor(row_factory=dict_row)
        if since:
            cur.execute("SELECT ts, author, body FROM commentary WHERE ts >= %s ORDER BY ts DESC LIMIT %s", (since, limit))
        else:
            cur.execute("SELECT ts, author, body FROM commentary ORDER BY ts DESC LIMIT %s", (limit,))
        return [{"ts": r["ts"].isoformat(), "author": r["author"], "body": r["body"]} for r in cur.fetchall()]


def list_trades(since: datetime | None = None, limit: int = 100) -> list[dict]:
    with _pool().connection() as c:
        cur = c.cursor(row_factory=dict_row)
        if since:
            cur.execute("SELECT ts, symbol, qty, price, fee, strategy FROM fills WHERE ts >= %s ORDER BY ts DESC LIMIT %s", (since, limit))
        else:
            cur.execute("SELECT ts, symbol, qty, price, fee, strategy FROM fills ORDER BY ts DESC LIMIT %s", (limit,))
        return [{"ts": r["ts"].isoformat(), "symbol": r["symbol"], "qty": float(r["qty"]),
                 "price": float(r["price"]), "fee": float(r["fee"]), "strategy": r["strategy"]} for r in cur.fetchall()]


# --- redis: swarm heartbeat watchdog (PRD §7.6) ----------------------------

def set_heartbeat(now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).isoformat()
    assert rds is not None, "call connect() first"
    rds.set(_HEARTBEAT_KEY, ts)
    return ts


def last_heartbeat() -> datetime | None:
    assert rds is not None, "call connect() first"
    raw = rds.get(_HEARTBEAT_KEY)
    return datetime.fromisoformat(raw) if raw else None
