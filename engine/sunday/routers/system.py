"""/api/system — runtime introspection; today just the time/timezone anchor.

Born from PRD-001: an agent that only ever sees zone-less wall-clock strings
cannot tell local time from UTC (the swarm read its UTC+8 harness stamps as UTC
and reported a phantom 8-hour clock skew). This endpoint is the self-serve
cross-check: the zone-free epoch, the same instant rendered in UTC and local
form, the zone name/offset, and the Binance↔local offset Sunday itself signs
requests with.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter

from .. import exchange

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/time")
def system_time() -> dict:
    now = datetime.now().astimezone()
    off = now.strftime("%z")  # "+0800" → reported as "+08:00"
    return {
        "epoch_ms": int(time.time() * 1000),
        "utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "local": now.isoformat(timespec="seconds"),
        "tz": now.tzname() or "",
        "utc_offset": f"{off[:3]}:{off[3:]}" if off else "",
        "binance_clock": exchange.clock_info(),
    }
