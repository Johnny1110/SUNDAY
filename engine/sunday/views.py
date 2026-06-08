"""Pure response builders + the lever state-machine, separated from FastAPI.

Keeping the HTTP *logic* here (and the FastAPI decorators + DB calls thin in
app.py) is what lets the milestone-3 acceptance behaviours be unit-tested without
a server or a database:

  * ``signals_view``  — the /signals decision panel (M3-T1): regime + each
    candidate strategy's vote, derived, so an agent never re-computes indicators.
  * ``status_view``   — /status enhanced with as_of_ts + last_lever + a votes
    summary (M3-T1/T4) so the agent can tell whether its view is stale.
  * ``apply_strategy`` — the defensive /strategy state machine (M3-T4): reason
    required, idempotent set, and ``expected_current`` optimistic concurrency that
    turns a stale switch into a correctable 409 instead of a silent mis-set.

app.py performs side effects (DB writes, repositioning) only when ``apply_strategy``
returns 200 + applied=True; the pure function never touches I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import regime as rg
from . import risk
from . import strategy as strat
from .market import Candles


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


def votes_summary(candles: Candles) -> list[dict]:
    """One terse line per candidate strategy, for /status (the full panel is /signals)."""
    return [{"strategy": v.strategy, "vote": v.vote, "confidence": round(v.confidence, 3)}
            for v in strat.vote_all(candles)]


def signals_view(symbol: str, candles: Candles, active_strategy: str, as_of: datetime | None = None) -> dict:
    """The /signals decision-support panel (M3-T1)."""
    return {
        "as_of_ts": _iso(as_of),
        "symbol": symbol,
        "regime": rg.classify(candles).as_dict(),
        "active": active_strategy,
        "votes": [v.as_dict() for v in strat.vote_all(candles)],
    }


def status_view(state: dict, candles: Candles | None = None, as_of: datetime | None = None) -> dict:
    """/status enhanced. ``state`` carries the live engine fields; this adds the
    milestone-3 legibility bits (as_of_ts, last_lever, votes summary)."""
    out = dict(state)
    out["as_of_ts"] = _iso(as_of)
    out.setdefault("last_lever", state.get("last_lever"))
    if candles is not None:
        out["votes"] = votes_summary(candles)
    return out


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


def apply_envelope(current: dict, updates: dict, reason: str | None) -> tuple[dict, int]:
    """Defensive /envelope decision (M1.1 lever). Partial updates allowed; every
    supplied field must be a positive number; unknown keys are ignored rather than
    failing. Returns (body, http_status); app.py persists + applies on (200, applied).
    reason is mandatory (stored for the operator, like a strategy switch)."""
    if not reason or not reason.strip():
        return ({"ok": False, "error": "reason_required",
                 "message": "a reason is mandatory — it is stored for the operator"}, 400)
    merged = dict(current)
    for k, v in (updates or {}).items():
        if k not in risk.Envelope.FIELDS:
            continue  # ignore unknown keys
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            return ({"ok": False, "error": "invalid_envelope",
                     "message": f"{k} must be a positive number"}, 400)
        merged[k] = float(v)
    return ({"ok": True, "applied": merged != current,
             "resulting_status": {"envelope": merged, "reason": reason}}, 200)
