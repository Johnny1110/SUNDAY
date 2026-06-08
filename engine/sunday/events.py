"""Outbound webhook events: Sunday → swarm (RP-9 `POST /api/swarm/{ref}/event`).

PURE + stdlib (urllib, no httpx): builders assemble the payload, `post` fires it.
Events are **self-sufficient** (PRD §7.9 / M3-T5): the payload carries a status
snapshot + the rationale + a suggested_action, so a webhook-woken agent can size
up the situation on its first turn without a round-trip.

The live `notify()` (which also logs to webhook_log + stamps last_event_ts) lives
in `engine.py`, where the store is in scope — keeping this module import-pure so
the payload shape + transport are unit-testable with the stdlib alone. `post`
NEVER raises: Sunday must keep trading even when the swarm is unreachable.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

# regime label → which strategy that regime favours (carried as suggested_action).
_REGIME_HINT = {
    "trending": "趨勢盤 → 建議切到 momentum（順勢）",
    "ranging": "震盪盤 → 建議切到 mean_reversion（逆勢）",
    "volatile": "高波動 → 建議 flat 觀望，避免被掃",
    "unknown": "盤性未明 → 維持現狀、觀望",
}


def build_event(
    event_type: str, title: str, body: str,
    status: Any = None, rationale: str | None = None,
    suggested_action: str | None = None, to: str = "leader",
) -> dict:
    """Assemble a self-sufficient webhook payload: {title, body, data, to}."""
    return {
        "title": title,
        "body": body,
        "data": {
            "event_type": event_type,
            "status": status,
            "rationale": rationale,
            "suggested_action": suggested_action,
        },
        "to": to,
    }


def regime_shift_event(prev_label: str | None, regime, status: Any = None) -> dict:
    """`regime_shift`: prev → new label, with the read's rationale + a matching hint.

    `regime` is a regime.RegimeRead (has .label + .rationale)."""
    title = f"regime shift：{prev_label} → {regime.label}"
    hint = _REGIME_HINT.get(regime.label, _REGIME_HINT["unknown"])
    return build_event("regime_shift", title, regime.rationale,
                       status=status, rationale=regime.rationale, suggested_action=hint)


def engine_degraded_event(detail: str, status: Any = None) -> dict:
    """`engine_degraded`: the engine can't trade — tell the leader to look (no HTTP restart)."""
    return build_event(
        "engine_degraded", "engine degraded", detail, status=status,
        rationale=detail,
        suggested_action="引擎異常 → 查 /status 對帳；Sunday 無 HTTP 重啟端點，請通報 User 重啟服務，"
                         "其間可 POST /halt {mode:'safe'} 凍新倉保護現有部位",
    )


def risk_breach_event(detail: str, status: Any = None) -> dict:
    """`risk_breach`: a deterministic fuse fired (e.g. drawdown) — leader must review."""
    return build_event(
        "risk_breach", "risk breach", detail, status=status,
        rationale=detail, suggested_action="風控熔斷已動作 → 複盤曝險，考慮縮封套或 halt",
    )


def safe_mode_event(detail: str, status: Any = None) -> dict:
    """`safe_mode_entered`: heartbeat timed out → new entries frozen."""
    return build_event(
        "safe_mode_entered", "safe-mode entered", detail, status=status,
        rationale=detail, suggested_action="腦死保護啟動 → 恢復 heartbeat 後再解除",
    )


_DESK_HINT = {
    "funding_extreme": "資金費極端 → 查 /desk?symbol=，評估收 carry 還是站旁邊（小心反身性逆轉）",
    "oi_surge": "持倉劇變 → 查 /desk?symbol=，評估方向與擁擠度",
    "basis_stretch": "基差拉伸 → 查 /desk?symbol=，評估反身性風險",
    "vol_spike": "波動跳升 → 查 /desk?symbol=，慎開倉",
    "notable": "此標的此刻值得注意 → 查 /desk?symbol=，評估是否值得一個 thesis",
}


def notable_event(symbol: str, event_type: str, driver: str, score: float,
                  metrics: dict | None = None, status: Any = None) -> dict:
    """`funding_extreme`/`oi_surge`/`basis_stretch`/`vol_spike`: Sunday's notable-score
    wake — a symbol is doing something worth a research round (milestone-4 T2)."""
    m = metrics or {}
    body = (f"{symbol} notable={score:.2f}（driver: {driver}）｜"
            f"funding {m.get('funding_annual_pct')}%/yr, basis {m.get('basis_bps')}bps")
    return build_event(event_type, f"notable: {symbol} · {driver}", body, status=status,
                       rationale=body, suggested_action=_DESK_HINT.get(event_type, _DESK_HINT["notable"]))


def _build_request(url: str, payload: dict) -> urllib.request.Request:
    """A JSON POST request (data set → method defaults to POST)."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    return req


def post(url: str, payload: dict, timeout: float = 3.0) -> tuple[int | None, bool]:
    """Fire-and-forget POST. Returns (http_status, ok); never raises."""
    try:
        with urllib.request.urlopen(_build_request(url, payload), timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return status, 200 <= status < 300
    except Exception:
        return None, False  # swarm unreachable — caller logs it and carries on
