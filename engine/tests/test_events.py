"""Unit tests for the webhook event builders + stdlib transport."""

import json
import unittest

from sunday import events
from sunday.regime import RegimeRead


class TestBuildEvent(unittest.TestCase):
    def test_self_sufficient_payload_shape(self):
        ev = events.build_event(
            "regime_shift", title="t", body="b",
            status={"strategy": "momentum"}, rationale="why", suggested_action="do x",
        )
        self.assertEqual(set(ev), {"title", "body", "data", "to"})
        self.assertEqual(ev["to"], "leader")
        self.assertEqual(ev["data"]["event_type"], "regime_shift")
        self.assertEqual(ev["data"]["status"], {"strategy": "momentum"})
        self.assertEqual(ev["data"]["rationale"], "why")
        self.assertEqual(ev["data"]["suggested_action"], "do x")

    def test_regime_shift_event_suggests_matching_strategy(self):
        rr = RegimeRead("ranging", 18.0, 0.9, "ADX low → 震盪")
        ev = events.regime_shift_event("trending", rr, status={"alive": True})
        self.assertIn("mean_reversion", ev["data"]["suggested_action"])  # ranging → mean_reversion
        self.assertEqual(ev["data"]["status"], {"alive": True})
        self.assertIn("trending → ranging", ev["title"])

    def test_engine_degraded_event(self):
        ev = events.engine_degraded_event("exchange timeout")
        self.assertEqual(ev["data"]["event_type"], "engine_degraded")
        self.assertIn("restart", ev["data"]["suggested_action"].lower())


class TestTransport(unittest.TestCase):
    def test_build_request_is_json_post(self):
        req = events._build_request("http://x/y", {"a": 1})
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(req.data), {"a": 1})

    def test_post_never_raises_on_bad_host(self):
        # unroutable → returns (None, False), does not raise (fire-and-forget)
        status, ok = events.post("http://127.0.0.1:1/nope", {"x": 1}, timeout=0.2)
        self.assertFalse(ok)
        self.assertIsNone(status)


if __name__ == "__main__":
    unittest.main()
