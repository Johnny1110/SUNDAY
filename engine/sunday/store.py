"""Local durable state — a single SQLite file (milestone-6).

The proxy is almost stateless: the exchange is the source of truth for the book,
mainnet for market data. The only thing Sunday must remember across restarts is
**alerts** (req 6) plus a little monitor config (req 5). So Postgres + Redis are
gone; this is stdlib ``sqlite3``, one file, created on boot.

Concurrency (load-bearing): FastAPI serves sync endpoints from a threadpool, so the
one connection is shared across threads (``check_same_thread=False``). A single
SQLite connection is NOT safe for concurrent use, and concurrent writers deadlock /
raise "database is locked". So **every** access — reads included — is serialized
through one write mutex ``_LOCK``. It is a ``RLock`` (reentrant) on purpose: a write
helper such as ``create_alert`` re-reads via ``get_alert`` while still holding the
lock, which a plain ``Lock`` would self-deadlock on. WAL + ``busy_timeout`` add a
second line of defence (readers never block the writer; contention waits, not errors).

``connect(path)`` takes the path explicitly (rather than importing config) so the
alert/monitor logic stays unit-testable against ``:memory:``.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

_conn: sqlite3.Connection | None = None
_LOCK = threading.RLock()  # reentrant write mutex — serializes ALL connection access

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    kind            TEXT NOT NULL,          -- price_above | price_below | pct_move
    threshold       REAL NOT NULL,          -- price (above/below) or |pct| (pct_move)
    ref_price       REAL,                   -- pct_move: price captured at creation
    note            TEXT,
    status          TEXT NOT NULL DEFAULT 'active',  -- active | triggered
    created_at      TEXT NOT NULL,
    triggered_at    TEXT,
    triggered_price REAL
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts (symbol) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);

-- Order journal (req) + audit log (BUG-03): the agent's rationale ("memo"), the exact
-- params, WHO did it (agent = self-reported X-Agent header) and WHAT kind of write it
-- was (action: order | protection | close | cancel) for every order-book mutation, so
-- the position query can join the WHY back and anomalous orders are attributable.
CREATE TABLE IF NOT EXISTS order_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    order_id     TEXT,
    side         TEXT,
    type         TEXT,
    qty          REAL,
    notional_usd REAL,
    price        REAL,
    leverage     INTEGER,
    margin_mode  TEXT,
    reduce_only  INTEGER,        -- 0 / 1
    take_profit  REAL,
    stop_loss    REAL,
    memo         VARCHAR(300),   -- length enforced at the API layer (sqlite ignores it)
    agent        TEXT,           -- X-Agent header at write time; null = caller didn't say
    action       TEXT NOT NULL DEFAULT 'order'  -- order | protection | close | cancel
);
CREATE INDEX IF NOT EXISTS idx_order_log_symbol ON order_log (symbol, id DESC);

-- Work journal (req): the reviewer's daily reports, persisted so the User can read
-- the team's work log in the dashboard. Body is markdown; `date` is the logical
-- report day (a report may be written just after midnight for the prior session).
CREATE TABLE IF NOT EXISTS journal (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,                       -- created (server UTC, ISO)
    date   TEXT,                                -- logical report date (YYYY-MM-DD)
    author TEXT NOT NULL DEFAULT 'reviewer',
    title  TEXT,
    body   TEXT NOT NULL                        -- markdown report
);
CREATE INDEX IF NOT EXISTS idx_journal_recent ON journal (id DESC);

-- Agent memory warehouse (replaces the file-based MEMORY.md / RESEARCH.md): one
-- long-term markdown doc per agent, overwritten wholesale. Each agent reads its doc on
-- wake (GET /api/memory/{agent}) and writes it back at session end (PUT). One row/agent.
CREATE TABLE IF NOT EXISTS memory (
    agent      TEXT PRIMARY KEY,
    content    TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);

-- User-facing reports: friday posts a notice here when something important happens
-- (large profit / large loss / system error). Shown on the dashboard Reports page,
-- newest first. Body is markdown, unbounded — clarity over brevity.
CREATE TABLE IF NOT EXISTS report (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT NOT NULL,                        -- created (server UTC, ISO)
    kind  TEXT NOT NULL DEFAULT 'info',         -- info | profit | loss | system
    title TEXT,
    body  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_report_recent ON report (id DESC);

-- Equity snapshots: the poll loop records account equity periodically so drawdown is
-- computable against a high-water mark (the HWM itself lives in kv and survives the
-- snapshot window pruning). Without this, "max drawdown" in the risk consensus is
-- unenforceable — no one holds equity history.
CREATE TABLE IF NOT EXISTS equity_snap (
    ts     TEXT PRIMARY KEY,                    -- snapshot time (server UTC, ISO)
    equity REAL NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive upgrades for DBs created before a column existed (CREATE TABLE IF NOT
    EXISTS never alters). order_log: + agent / action (BUG-03 audit log) — pre-existing
    rows take action 'order', which is what they all were."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(order_log)")}
    if "agent" not in cols:
        conn.execute("ALTER TABLE order_log ADD COLUMN agent TEXT")
    if "action" not in cols:
        conn.execute("ALTER TABLE order_log ADD COLUMN action TEXT NOT NULL DEFAULT 'order'")


def connect(path: str) -> None:
    global _conn
    with _LOCK:
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")    # readers don't block the single writer
        _conn.execute("PRAGMA busy_timeout=5000")   # wait up to 5s on contention, don't error
        _conn.executescript(_SCHEMA)
        _migrate(_conn)
        _conn.commit()


def close() -> None:
    global _conn
    with _LOCK:
        if _conn is not None:
            _conn.close()
            _conn = None


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("store not connected — call connect() first")
    return _conn


# Every table the proxy persists — the unit a test reset wipes (schema stays; rows go).
_TABLES = ("alerts", "kv", "order_log", "journal", "memory", "report", "equity_snap")


def reset() -> dict[str, int]:
    """Wipe ALL local durable state — alerts, kv (monitor config), order log, work
    journal — for a clean test slate. Irreversible; schema is preserved. Returns the
    row count removed per table. (Table names are fixed constants, so the f-string is
    not an injection surface.)"""
    with _LOCK:
        counts = {t: _db().execute(f"DELETE FROM {t}").rowcount for t in _TABLES}
        _db().commit()
        return counts


# --------------------------------------------------------------------------
# Alerts (req 6)
# --------------------------------------------------------------------------

def create_alert(symbol: str, kind: str, threshold: float,
                 ref_price: float | None = None, note: str | None = None) -> dict:
    with _LOCK:
        cur = _db().execute(
            "INSERT INTO alerts (symbol, kind, threshold, ref_price, note, status, created_at) "
            "VALUES (?,?,?,?,?, 'active', ?)",
            (symbol, kind, threshold, ref_price, note, _now()),
        )
        _db().commit()
        return get_alert(cur.lastrowid)  # reentrant: still inside _LOCK


def get_alert(alert_id: int) -> dict | None:
    with _LOCK:
        row = _db().execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return dict(row) if row else None


def list_alerts(status: str | None = None) -> list[dict]:
    with _LOCK:
        if status:
            rows = _db().execute(
                "SELECT * FROM alerts WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = _db().execute("SELECT * FROM alerts ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def active_alerts() -> list[dict]:
    """Alerts the realtime engine must still evaluate."""
    return list_alerts(status="active")


def delete_alert(alert_id: int) -> bool:
    with _LOCK:
        cur = _db().execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        _db().commit()
        return cur.rowcount > 0


def mark_triggered(alert_id: int, price: float) -> None:
    with _LOCK:
        _db().execute(
            "UPDATE alerts SET status='triggered', triggered_at=?, triggered_price=? WHERE id=?",
            (_now(), price, alert_id),
        )
        _db().commit()


# --------------------------------------------------------------------------
# kv — small monitor/config flags
# --------------------------------------------------------------------------

def kv_get(k: str, default: str | None = None) -> str | None:
    with _LOCK:
        row = _db().execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
        return row["v"] if row else default


def kv_set(k: str, v: str) -> None:
    with _LOCK:
        _db().execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (k, v),
        )
        _db().commit()


# --------------------------------------------------------------------------
# Order journal — agent rationale + params per order (req)
# --------------------------------------------------------------------------

def record_order(symbol: str, order_id: str | None, memo: str | None, order: dict,
                 agent: str | None = None, action: str = "order") -> None:
    """Log one order-book mutation: params (one column each) + the agent's memo (req)
    + the operator and action kind (BUG-03 audit log)."""
    with _LOCK:
        _db().execute(
            "INSERT INTO order_log (ts, symbol, order_id, side, type, qty, notional_usd, price, "
            "leverage, margin_mode, reduce_only, take_profit, stop_loss, memo, agent, action) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), symbol, str(order_id) if order_id is not None else None,
             order.get("side"), order.get("type"), order.get("qty"), order.get("notional_usd"),
             order.get("price"), order.get("leverage"), order.get("margin_mode"),
             1 if order.get("reduce_only") else 0, order.get("take_profit"), order.get("stop_loss"),
             memo, agent, action),
        )
        _db().commit()


def latest_order(symbol: str) -> dict | None:
    """The most recent logged ENTRY order for a symbol (memo + params) — what the
    position query joins to surface the agent's rationale to the User. Audit rows
    (protection / close / cancel) never displace the entry's memo."""
    with _LOCK:
        row = _db().execute(
            "SELECT * FROM order_log WHERE symbol = ? AND action = 'order' "
            "ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["reduce_only"] = bool(d.get("reduce_only"))
    return d


def agents_by_order_id(symbol: str | None = None) -> dict[str, str]:
    """order_id → agent for every CREATING audit row that named its operator — the
    join the /api/account listings use (BUG-03). Cancel rows reference someone ELSE's
    order id and must not re-attribute it."""
    q = ("SELECT order_id, agent FROM order_log WHERE agent IS NOT NULL "
         "AND order_id IS NOT NULL AND action != 'cancel'")
    args: tuple = ()
    if symbol:
        q += " AND symbol = ?"
        args = (symbol,)
    with _LOCK:
        rows = _db().execute(q + " ORDER BY id", args).fetchall()
        return {str(r["order_id"]): r["agent"] for r in rows}


# --------------------------------------------------------------------------
# Work journal — reviewer's daily reports, shown to the User (req)
# --------------------------------------------------------------------------

def add_journal(body: str, title: str | None = None,
                date: str | None = None, author: str | None = "reviewer") -> dict:
    """Persist one work-log entry (markdown body). `date` defaults to today (UTC)."""
    with _LOCK:
        cur = _db().execute(
            "INSERT INTO journal (ts, date, author, title, body) VALUES (?,?,?,?,?)",
            (_now(), date or _now()[:10], author or "reviewer", title, body),
        )
        _db().commit()
        return get_journal(cur.lastrowid)  # reentrant: still inside _LOCK


def get_journal(entry_id: int) -> dict | None:
    with _LOCK:
        row = _db().execute("SELECT * FROM journal WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None


def list_journal(author: str | None = None) -> list[dict]:
    """All work-log entries newest-first (optionally one author), for the paged UI."""
    with _LOCK:
        if author:
            rows = _db().execute(
                "SELECT * FROM journal WHERE author = ? ORDER BY id DESC", (author,)).fetchall()
        else:
            rows = _db().execute("SELECT * FROM journal ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Agent memory warehouse — one long-term markdown doc per agent (replaces the
# file-based MEMORY.md / RESEARCH.md). Read on wake, overwritten at session end.
# --------------------------------------------------------------------------

def get_memory(agent: str) -> dict | None:
    with _LOCK:
        row = _db().execute(
            "SELECT agent, content, updated_at FROM memory WHERE agent = ?", (agent,)).fetchone()
        return dict(row) if row else None


def set_memory(agent: str, content: str) -> dict:
    """Overwrite an agent's memory doc (upsert). Returns the stored row."""
    ts = _now()
    with _LOCK:
        _db().execute(
            "INSERT INTO memory (agent, content, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(agent) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
            (agent, content, ts),
        )
        _db().commit()
    return {"agent": agent, "content": content, "updated_at": ts}


def list_memory() -> list[dict]:
    """All stored memory docs (used by the dashboard memory index)."""
    with _LOCK:
        rows = _db().execute("SELECT agent, content, updated_at FROM memory").fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Equity snapshots + high-water mark — drawdown support for risk-monitor
# --------------------------------------------------------------------------

_EQUITY_KEEP_DAYS = 30  # snapshot window; the HWM in kv outlives pruning


def add_equity_snap(equity: float, keep_days: int = _EQUITY_KEEP_DAYS) -> None:
    """Record an equity snapshot, advance the high-water mark, prune old rows."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=keep_days)).isoformat()
    with _LOCK:
        _db().execute(
            "INSERT INTO equity_snap (ts, equity) VALUES (?, ?) "
            "ON CONFLICT(ts) DO UPDATE SET equity = excluded.equity",
            (now.isoformat(), equity),
        )
        _db().execute("DELETE FROM equity_snap WHERE ts < ?", (cutoff,))
        hwm = kv_get("equity_hwm")  # reentrant: still inside _LOCK
        if hwm is None or equity > float(hwm):
            kv_set("equity_hwm", repr(equity))
            kv_set("equity_hwm_ts", now.isoformat())
        _db().commit()


def equity_hwm() -> tuple[float, str | None] | None:
    """(high_water, ts) or None when no snapshot has ever been taken."""
    with _LOCK:
        hwm = kv_get("equity_hwm")
        return (float(hwm), kv_get("equity_hwm_ts")) if hwm is not None else None


def equity_snaps(limit: int = 500) -> list[dict]:
    """Recent equity snapshots, newest first."""
    with _LOCK:
        rows = _db().execute(
            "SELECT ts, equity FROM equity_snap ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

def add_report(title: str, body: str, kind: str = "info") -> dict:
    """Persist one report (markdown body). Returns the stored row."""
    ts = _now()
    with _LOCK:
        cur = _db().execute(
            "INSERT INTO report (ts, kind, title, body) VALUES (?,?,?,?)", (ts, kind, title, body))
        _db().commit()
        rid = cur.lastrowid
    return {"id": rid, "ts": ts, "kind": kind, "title": title, "body": body}


def get_report(report_id: int) -> dict | None:
    with _LOCK:
        row = _db().execute("SELECT * FROM report WHERE id = ?", (report_id,)).fetchone()
        return dict(row) if row else None


def list_reports(kind: str | None = None) -> list[dict]:
    """Reports newest-first (optionally one kind), for the paged dashboard."""
    with _LOCK:
        if kind:
            rows = _db().execute(
                "SELECT * FROM report WHERE kind = ? ORDER BY id DESC", (kind,)).fetchall()
        else:
            rows = _db().execute("SELECT * FROM report ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
