"""PRD-005 regression — /api/klines/indicators must degrade, not hang.

The incident: a transient upstream stall turned every indicators request into a full
client timeout (`context deadline exceeded`), and retries just queued again. Contract
now: candles are cached per (symbol, interval, limit) with an interval-scaled TTL, and
an upstream failure serves the last-good panel marked `stale` instead of erroring —
so a retry after a hang answers instantly. Needs engine deps (fastapi/ccxt mocks).
"""

import unittest
from unittest import mock

from fastapi import HTTPException

from sunday.routers import klines
from sunday.ttlcache import StaleCache

KLINES_200 = [[1750500000000 + i * 3600_000, "100", "101", "99", "100.5", "12"]
              for i in range(200)]


class TestIntervalTtl(unittest.TestCase):
    def test_scales_with_interval_and_clamps(self):
        self.assertEqual(klines._ttl("1m"), 10)     # floor: stays near-live
        self.assertEqual(klines._ttl("5m"), 15)
        self.assertEqual(klines._ttl("15m"), 45)
        self.assertEqual(klines._ttl("1h"), 180)    # PRD-005: ≤5min recompute is plenty
        self.assertEqual(klines._ttl("4h"), 300)    # ceiling
        self.assertEqual(klines._ttl("1d"), 300)
        self.assertEqual(klines._ttl("1M"), 300)

    def test_every_supported_interval_has_a_ttl(self):
        for iv in klines.INTERVALS:
            self.assertGreaterEqual(klines._ttl(iv), 10, iv)


class TestIndicatorPanelCache(unittest.TestCase):
    def setUp(self):
        # fresh cache per test — the module-level one is process-wide state
        self.patch_cache = mock.patch.object(klines, "_CANDLES", StaleCache())
        self.patch_cache.start()
        self.addCleanup(self.patch_cache.stop)

    def test_second_call_within_ttl_does_not_refetch(self):
        with mock.patch.object(klines.exchange, "fetch_ohlcv",
                               return_value=KLINES_200) as fetch:
            a = klines.indicator_panel("BTCUSDT", "1h", which="rsi,ema")
            b = klines.indicator_panel("BTCUSDT", "1h", which="rsi,ema")
        self.assertEqual(fetch.call_count, 1)
        self.assertNotIn("stale", a)
        self.assertEqual(a["indicators"]["rsi"], b["indicators"]["rsi"])

    def test_intervals_cache_independently(self):
        with mock.patch.object(klines.exchange, "fetch_ohlcv",
                               return_value=KLINES_200) as fetch:
            klines.indicator_panel("BTCUSDT", "1h", which="rsi,ema")
            klines.indicator_panel("BTCUSDT", "4h", which="rsi,ema")
        self.assertEqual(fetch.call_count, 2)

    def test_upstream_failure_serves_last_good_marked_stale(self):
        # The PRD-005 scenario: panel fetched earlier, then the upstream stalls.
        with mock.patch.object(klines.exchange, "fetch_ohlcv", return_value=KLINES_200):
            klines.indicator_panel("BTCUSDT", "1h", which="rsi,ema")
        key = ("BTCUSDT", "1h", 200)
        v, at = klines._CANDLES._d[key]
        klines._CANDLES._d[key] = (v, at - 10_000)   # age the entry far past any TTL
        with mock.patch.object(klines.exchange, "fetch_ohlcv",
                               side_effect=TimeoutError("upstream stall")):
            out = klines.indicator_panel("BTCUSDT", "1h", which="rsi,ema")
        self.assertIs(out["stale"], True)
        self.assertGreater(out["stale_age_s"], 0)
        self.assertIn("rsi", out["indicators"])     # full panel still served

    def test_upstream_failure_cold_is_a_clean_502(self):
        with mock.patch.object(klines.exchange, "fetch_ohlcv",
                               side_effect=TimeoutError("upstream stall")):
            with self.assertRaises(HTTPException) as ctx:
                klines.indicator_panel("BTCUSDT", "1h", which="rsi,ema")
        self.assertEqual(ctx.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
