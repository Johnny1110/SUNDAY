"""StaleCache — a TTL cache that degrades instead of failing (PRD-005).

The indicators path had no degradation story: any upstream stall (network blip,
Binance latency spike, rate-limit queueing under agent fan-out) became a full client
timeout, and a retry just queued behind the same stall. This cache gives read paths
the markets-router semantics as a reusable type: serve fresh within ``ttl``, reload on
expiry, and when the loader FAILS serve the last-good value flagged stale — so the
retry after a hang answers instantly with data that is still decision-useful.

Stdlib-only and clock-injectable (invariant 6). Plain dict ops under the GIL — same
concurrency stance as the markets-router cache; a cold-key thundering herd does one
duplicate upstream call per concurrent thread, which the TTL absorbs right after.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Hashable


class StaleCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._d: dict[Hashable, tuple[Any, float]] = {}   # key -> (value, loaded_at)
        self._clock = clock

    def get(self, key: Hashable, ttl: float,
            loader: Callable[[], Any]) -> tuple[Any, float, bool]:
        """``(value, age_seconds, stale)`` for ``key``.

        Fresh hit (age ≤ ttl) → cached value, no load. Expired/missing → ``loader()``;
        on success the value is cached (age 0). On loader failure: a cached value of
        any age is served with ``stale=True``; with nothing cached the error raises
        (the caller's normal error path — a clean 502 upstream)."""
        now = self._clock()
        hit = self._d.get(key)
        if hit is not None and now - hit[1] <= ttl:
            return hit[0], now - hit[1], False
        try:
            value = loader()
        except Exception:
            if hit is not None:
                return hit[0], now - hit[1], True
            raise
        self._d[key] = (value, now)
        return value, 0.0, False
