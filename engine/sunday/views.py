"""Pure response builders + the lever state-machine, separated from FastAPI.

Keeping the HTTP *logic* here (and the FastAPI decorators + DB calls thin in
app.py) is what lets the lever/panel behaviours be unit-tested without a server
or a database:

  * ``risk_view``      — the /risk panel: active caps vs a best-effort live read
    + per-cap utilization + current violations + the recent deterministic-fuse log.
  * ``apply_strategy`` — the defensive /strategy state machine: reason required,
    idempotent set, and ``expected_current`` optimistic concurrency that turns a
    stale switch into a correctable 409 instead of a silent mis-set.
  * ``validate_thesis`` — the defensive /thesis input check (direction in range,
    conviction a number in [0,1], reason mandatory) — milestone-4's thesis-lever guard.

app.py performs side effects (DB writes, repositioning) only when these return
200 + applied/ok; the pure functions never touch I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import strategy as strat


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


def risk_view(envelope: dict, current: dict, recent_events: list[dict],
              as_of: datetime | None = None) -> dict:
    """The /risk panel (pure): caps vs current + per-cap utilization + live violations.

    ``envelope`` = the active hard caps; ``current`` = a best-effort live read
    (equity / position_usd / exposure_usd / leverage / drawdown_pct). Kept pure so
    the utilization + violation logic is unit-testable without an exchange or DB —
    app.py only gathers the inputs (store + ccxt) and hands them here.
    """
    def ratio(v: float, cap: float) -> float | None:
        return round(v / cap, 4) if cap else None

    pos = current.get("position_usd") or 0.0
    exp = current.get("exposure_usd") or 0.0
    lev = current.get("leverage") or 0.0      # effective leverage = exposure/equity
    dd = current.get("drawdown_pct") or 0.0
    violations: list[str] = []
    if pos > envelope["max_position_usd"]:
        violations.append("size_cap")
    if exp > envelope["max_total_exposure_usd"]:
        violations.append("exposure_cap")
    if lev > envelope["max_leverage"]:
        violations.append("leverage_cap")
    if dd >= envelope["max_drawdown_pct"]:
        violations.append("drawdown")
    return {
        "as_of_ts": _iso(as_of),
        "envelope": envelope,
        "current": {
            "equity": current.get("equity") or 0.0,
            "position_usd": pos, "exposure_usd": exp,
            "leverage": round(lev, 3), "drawdown_pct": round(dd, 4),
        },
        "utilization": {
            "position": ratio(pos, envelope["max_position_usd"]),
            "exposure": ratio(exp, envelope["max_total_exposure_usd"]),
            "leverage": ratio(lev, envelope["max_leverage"]),
            "drawdown": ratio(dd, envelope["max_drawdown_pct"]),
        },
        "violations": violations,
        "recent_events": recent_events,
    }


def apply_strategy(current_strategy: str, requested: str, reason: str | None,
                   expected_current: str | None, symbol: str,
                   valid: tuple[str, ...] = strat.VALID_STRATEGIES) -> tuple[dict, int]:
    """Defensive /strategy decision (M3-T4). Returns (body, http_status).

    app.py writes strategy_state + repositions only on (200, applied=True).
    """
    if requested not in valid:
        return ({"ok": False, "error": "invalid_strategy",
                 "message": f"unknown strategy {requested!r}; valid: {', '.join(valid)}"}, 400)
    if not reason or not reason.strip():
        return ({"ok": False, "error": "reason_required",
                 "message": "a reason is mandatory — it is stored for the operator (PRD §7.11)"}, 400)
    # Optimistic concurrency: the agent's view must be current (PRD §7.10 made mechanical).
    if expected_current is not None and expected_current != current_strategy:
        return ({"ok": False, "error": "stale",
                 "message": f"expected_current={expected_current!r} but active is {current_strategy!r}; re-read /status",
                 "current_status": {"symbol": symbol, "strategy": current_strategy}}, 409)
    applied = requested != current_strategy  # idempotent: same strategy = no-op, still ok
    return ({"ok": True, "applied": applied,
             "resulting_status": {"symbol": symbol, "strategy": requested, "reason": reason}}, 200)


def validate_thesis(direction: str, conviction, reason: str | None,
                    valid: tuple[str, ...] = ("long", "short", "flat")) -> tuple[dict, int]:
    """Defensive /thesis input check (milestone-4 T4). Returns (body, http_status).

    Pure: app.py only writes the thesis + reconciles on (200). reason is mandatory
    (User-visible, PRD §7.11); conviction must be a number in [0,1]."""
    if direction not in valid:
        return ({"ok": False, "error": "invalid_direction",
                 "message": f"direction must be one of {', '.join(valid)}"}, 400)
    try:
        c = float(conviction)
    except (TypeError, ValueError):
        return ({"ok": False, "error": "invalid_conviction", "message": "conviction must be a number"}, 400)
    if not 0.0 <= c <= 1.0:
        return ({"ok": False, "error": "invalid_conviction", "message": "conviction must be in [0, 1]"}, 400)
    if not reason or not reason.strip():
        return ({"ok": False, "error": "reason_required",
                 "message": "a rationale is mandatory — it is stored for the operator (PRD §7.11)"}, 400)
    return ({"ok": True}, 200)
