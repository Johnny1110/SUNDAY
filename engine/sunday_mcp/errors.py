"""Error rendering for tool results (S7, stdlib-only).

Two distinct shapes, mirroring the http_request philosophy the swarm already
knows:

  * upstream HTTP 4xx/5xx → a NORMAL tool result, body passed through verbatim
    — the engine's error text is the actionable part. Known Binance/engine
    codes get one extra hint line (same playbook as the operate-desk skill's
    error manual; single-sourced in _HINTS below, never re-explained).
  * connection-layer failure → a tool ERROR (the caller raises with
    UNREACHABLE_TEXT) so the agent knows to degrade, not to re-parse.
"""

from __future__ import annotations

from .client import Reply

UNREACHABLE_TEXT = ("sunday engine unreachable after retry — check GET /health; "
                    "fall back to http_request if urgent (see RUNBOOK.md)")

# Known-code hints (PRD-9.2 §3). Matched in order against the response body;
# first hit wins. Keep these one-liners aligned with the operate-desk skill.
_HINTS: tuple[tuple[str, str], ...] = (
    ("-4016", "→ price too far from mark; re-quote near current price or use market"),
    ("-1021", "→ clock skew; engine self-heals — if repeated, POST /api/reports kind=system"),
    ("-2011", "→ order id not found on either book; refresh open_orders first"),
)


def is_error(reply: Reply) -> bool:
    return reply.status >= 400


def upstream_error_text(reply: Reply) -> str:
    base = f"[sunday {reply.status}] {reply.text.strip()}"
    hint = _hint_for(reply)
    return f"{base}\n{hint}" if hint else base


def _hint_for(reply: Reply) -> str | None:
    body = reply.text
    for code, hint in _HINTS:
        if code in body:
            return hint
    if reply.status == 400 and "trigger" in body.lower():
        return "→ trigger price on the wrong side; engine blocked an instant-fill leg"
    if reply.status == 503:
        return "→ engine degraded; check GET /health — fall back to http_request per RUNBOOK.md"
    return None
