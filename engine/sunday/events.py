"""Outbound events: Sunday -> swarm via the RP-9 webhook (`POST /api/swarm/{ref}/event`).

notify() is the one function the engine needs to wake the swarm. It POSTs to the
evva webhook and logs every send to webhook_log. It NEVER raises if the swarm is
unreachable — Sunday must keep running even when the swarm is down.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from . import store
from .config import settings


def notify(event_type: str, title: str, body: str, data: dict | None = None, to: str = "leader") -> dict:
    payload = {"title": title, "body": body, "data": {**(data or {}), "event_type": event_type}, "to": to}
    http_status: int | None = None
    message_id: str | None = None
    try:
        resp = httpx.post(settings.evva_webhook_url, json=payload, timeout=3.0)
        http_status = resp.status_code
        try:
            message_id = resp.json().get("messageId")
        except Exception:
            pass
    except Exception:
        pass  # swarm webhook unreachable — log it and carry on
    store.record_webhook(event_type, to, title, body, http_status, message_id)
    store.set_last_event_ts(datetime.now(timezone.utc).isoformat())
    return {"event_type": event_type, "http_status": http_status, "message_id": message_id}
