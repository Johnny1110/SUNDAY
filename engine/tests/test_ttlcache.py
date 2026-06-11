"""Unit tests for the stale-on-error TTL cache (PRD-005, pure stdlib)."""

import unittest

from sunday.ttlcache import StaleCache


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestStaleCache(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cache = StaleCache(clock=self.clock)
        self.loads = 0

    def _loader(self):
        self.loads += 1
        return {"n": self.loads}

    def _boom(self):
        raise RuntimeError("upstream down")

    def test_fresh_hit_within_ttl_loads_once(self):
        v1, age1, stale1 = self.cache.get("k", 30, self._loader)
        self.clock.t += 10
        v2, age2, stale2 = self.cache.get("k", 30, self._loader)
        self.assertEqual(self.loads, 1)
        self.assertEqual(v1, v2)
        self.assertEqual((stale1, stale2), (False, False))
        self.assertEqual(age1, 0.0)
        self.assertEqual(age2, 10.0)

    def test_expiry_reloads(self):
        self.cache.get("k", 30, self._loader)
        self.clock.t += 31
        v, age, stale = self.cache.get("k", 30, self._loader)
        self.assertEqual(self.loads, 2)
        self.assertEqual((v["n"], age, stale), (2, 0.0, False))

    def test_loader_failure_serves_stale(self):
        self.cache.get("k", 30, self._loader)
        self.clock.t += 120
        v, age, stale = self.cache.get("k", 30, self._boom)
        self.assertEqual((v["n"], age, stale), (1, 120.0, True))

    def test_loader_failure_cold_raises(self):
        with self.assertRaises(RuntimeError):
            self.cache.get("k", 30, self._boom)

    def test_recovery_after_stale_replaces_value(self):
        self.cache.get("k", 30, self._loader)
        self.clock.t += 120
        self.cache.get("k", 30, self._boom)          # stale serve
        self.cache.get("k", 30, self._loader)        # upstream back → reload
        v, age, stale = self.cache.get("k", 30, self._loader)
        self.assertEqual((v["n"], stale), (2, False))
        self.assertEqual(self.loads, 2)

    def test_keys_are_independent(self):
        self.cache.get(("BTCUSDT", "1h"), 30, self._loader)
        self.cache.get(("BTCUSDT", "4h"), 30, self._loader)
        self.assertEqual(self.loads, 2)


if __name__ == "__main__":
    unittest.main()
