"""Outbound events — Sunday → swarm webhook (RP-9), self-sufficient (milestone-3 T5).

Sunday is the swarm's non-LLM teammate; a webhook is a letter into the leader's
inbox (RP-9 reuses the bus/drain machinery). The milestone-3 upgrade: make the
letter *self-sufficient* so the woken agent's first turn doesn't need a curl —
``data`` carries a ``status`` snapshot, the ``rationale`` (why this fired, with the
trigger indicators — PRD §7.9 hard requirement), and a ``suggested_action``. Good
mail = good hand-off, exactly like a worker→leader report.

Transport is stdlib ``urllib`` (no httpx/requests dep) and fire-and-forget: a
webhook must never block or crash the trading loop, so ``post`` swallows every
error and returns ``(status|None, ok)`` for the caller to log to ``webhook_log``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def build_event(event_type: str, *, title: str, body: str,
                status: dict | None = None, rationale: str | None = None,
                suggested_action: str | None = None, to: str | None = "leader",
                data: dict | None = None) -> dict:
    """Assemble the RP-9 webhook payload {title, body, data, to}, with the
    milestone-3 self-sufficient ``data`` fields folded in."""
    payload_data = dict(data or {})
    payload_data["event_type"] = event_type
    if status is not None:
        payload_data["status"] = status
    if rationale is not None:
        payload_data["rationale"] = rationale
    if suggested_action is not None:
        payload_data["suggested_action"] = suggested_action
    return {"title": title, "body": body, "data": payload_data, "to": to}


# Which strategy a regime suggests — drives suggested_action (legible, not binding;
# only the leader actually pulls the lever).
_REGIME_HINT = {
    "trending": ("momentum", "順勢"),
    "ranging": ("mean_reversion", "逆勢震盪"),
    "volatile": ("flat", "高波動，宜空手/減倉"),
}


def regime_shift_event(prev_label: str, regime_read, status: dict) -> dict:
    """Build a self-sufficient regime_shift letter for the leader."""
    label = regime_read.label
    strat, why = _REGIME_HINT.get(label, ("flat", "盤性不明"))
    suggested = (f"盤性由 {prev_label} → {label}（{why}）。考慮把 BTCUSDT 切到 {strat}；"
                 f"先 `curl :7777/signals` 複核各策略投票再決定，切策略附 reason。")
    return build_event(
        "regime_shift",
        title=f"Regime shift: {prev_label} → {label}",
        body=f"Sunday 偵測盤性改變（{prev_label} → {label}）。{regime_read.rationale}",
        status=status,
        rationale=regime_read.rationale,
        suggested_action=suggested,
    )


def engine_degraded_event(detail: str, status: dict | None = None) -> dict:
    """Sunday can't trade / exchange error — leader may need to restart."""
    return build_event(
        "engine_degraded",
        title="Engine degraded",
        body=f"Sunday 異常：{detail}。可能需要注意或 `POST /restart`。",
        status=status,
        rationale=detail,
        suggested_action="先 `curl :7777/status` 確認；持續異常則 `POST /restart`（非冪等、需確認）。",
    )


def _build_request(url: str, payload: dict) -> urllib.request.Request:
    body = json.dumps(payload).encode("utf-8")
    return urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )


def post(url: str, payload: dict, timeout: float = 2.0) -> tuple[int | None, bool]:
    """Fire-and-forget POST. Never raises — returns (http_status|None, ok)."""
    try:
        with urllib.request.urlopen(_build_request(url, payload), timeout=timeout) as resp:
            status = getattr(resp, "status", None)
            return status, (status is not None and 200 <= status < 300)
    except urllib.error.HTTPError as e:
        return e.code, False
    except Exception:
        return None, False
