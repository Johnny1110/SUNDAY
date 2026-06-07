"""Postgres pool + redis client + a tiny migration runner + the 1.0 DAO.

Runtime state (mode/rationale/heartbeat/regime) lives in redis; the trade ledger
(strategy_state/signals/orders/positions/risk_events/webhook_log) lives in postgres.
The exchange remains the source of truth for actual positions — these tables are
our attribution/audit record (modeling-grade).
"""

from __future__ import annotations

import pathlib
import time

import redis
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import settings

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"

pool: ConnectionPool | None = None
rds: redis.Redis | None = None


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


def close_open_positions(symbol: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE positions SET closed_at=now() WHERE symbol=%s AND closed_at IS NULL",
            (symbol,),
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
