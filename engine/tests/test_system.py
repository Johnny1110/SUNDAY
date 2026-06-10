"""GET /api/system/time — the time/timezone anchor (PRD-001; no network)."""

import unittest
from datetime import datetime

from sunday.routers import system


class TestSystemTime(unittest.TestCase):
    def test_shape_and_consistency(self):
        d = system.system_time()

        self.assertIsInstance(d["epoch_ms"], int)
        self.assertTrue(d["utc"].endswith("+00:00"), d["utc"])
        self.assertRegex(d["utc_offset"], r"^[+-]\d{2}:\d{2}$")

        utc = datetime.fromisoformat(d["utc"])
        local = datetime.fromisoformat(d["local"])
        # Two renderings of one instant, and both within a beat of epoch_ms.
        self.assertEqual(utc, local)
        self.assertAlmostEqual(utc.timestamp() * 1000, d["epoch_ms"], delta=2000)

        self.assertIn("offset_ms", d["binance_clock"])
        self.assertIn("synced", d["binance_clock"])

    def test_local_carries_the_reported_offset(self):
        d = system.system_time()
        self.assertTrue(d["local"].endswith(d["utc_offset"]), d)
