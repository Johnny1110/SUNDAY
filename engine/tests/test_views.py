"""Unit tests for the pure HTTP logic: /risk panel + defensive /strategy & /thesis levers."""

import unittest
from datetime import datetime, timezone

from sunday import views


FIXED = datetime(2026, 6, 8, 11, 0, tzinfo=timezone.utc)


ENV = {"max_position_usd": 2000.0, "max_total_exposure_usd": 4000.0,
       "max_leverage": 3.0, "max_drawdown_pct": 5.0, "stop_pct": 0.02}


class TestRiskView(unittest.TestCase):
    def test_within_caps_no_violations(self):
        cur = {"equity": 10000.0, "position_usd": 500.0, "exposure_usd": 500.0,
               "leverage": 0.05, "drawdown_pct": 1.0}
        v = views.risk_view(ENV, cur, [], as_of=FIXED)
        self.assertEqual(v["violations"], [])
        self.assertEqual(v["as_of_ts"], FIXED.isoformat())
        self.assertEqual(v["envelope"], ENV)
        self.assertAlmostEqual(v["utilization"]["position"], 0.25)   # 500/2000
        self.assertAlmostEqual(v["utilization"]["exposure"], 0.125)  # 500/4000

    def test_each_cap_breach_flagged(self):
        cur = {"equity": 1000.0, "position_usd": 2500.0, "exposure_usd": 5000.0,
               "leverage": 5.0, "drawdown_pct": 6.0}
        v = views.risk_view(ENV, cur, [])
        self.assertEqual(set(v["violations"]), {"size_cap", "exposure_cap", "leverage_cap", "drawdown"})

    def test_drawdown_at_limit_is_a_breach(self):
        cur = {"equity": 9500.0, "position_usd": 0.0, "exposure_usd": 0.0,
               "leverage": 0.0, "drawdown_pct": 5.0}  # exactly at the cap
        v = views.risk_view(ENV, cur, [])
        self.assertIn("drawdown", v["violations"])

    def test_recent_events_passed_through(self):
        events = [{"ts": FIXED.isoformat(), "type": "drawdown", "detail": {}, "action_taken": "flatten_and_lock"}]
        v = views.risk_view(ENV, {"equity": 0.0}, events)
        self.assertEqual(v["recent_events"], events)


class TestApplyStrategy(unittest.TestCase):
    def test_valid_switch_returns_resulting_state(self):
        body, code = views.apply_strategy("momentum", "mean_reversion", "regime turned", None, "BTCUSDT")
        self.assertEqual(code, 200)
        self.assertTrue(body["applied"])
        self.assertEqual(body["resulting_status"]["strategy"], "mean_reversion")  # A2: verify from response

    def test_idempotent_same_strategy_ok_not_applied(self):
        body, code = views.apply_strategy("flat", "flat", "stay flat", None, "BTCUSDT")
        self.assertEqual(code, 200)
        self.assertFalse(body["applied"])           # no-op, still ok

    def test_invalid_strategy_400(self):
        body, code = views.apply_strategy("momentum", "hodl", "x", None, "BTCUSDT")
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "invalid_strategy")

    def test_reason_required_400(self):
        body, code = views.apply_strategy("momentum", "flat", "  ", None, "BTCUSDT")
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "reason_required")

    def test_stale_expected_current_409_with_current(self):
        # A3: agent's view said momentum, but engine already moved to flat
        body, code = views.apply_strategy("flat", "mean_reversion", "switch", "momentum", "BTCUSDT")
        self.assertEqual(code, 409)
        self.assertEqual(body["error"], "stale")
        self.assertEqual(body["current_status"]["strategy"], "flat")

    def test_matching_expected_current_proceeds(self):
        body, code = views.apply_strategy("momentum", "flat", "go flat", "momentum", "BTCUSDT")
        self.assertEqual(code, 200)
        self.assertTrue(body["applied"])


class TestValidateThesis(unittest.TestCase):
    def test_valid(self):
        body, code = views.validate_thesis("long", 0.6, "funding flipped negative")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_invalid_direction(self):
        body, code = views.validate_thesis("sideways", 0.5, "x")
        self.assertEqual((code, body["error"]), (400, "invalid_direction"))

    def test_conviction_out_of_range(self):
        _, code = views.validate_thesis("long", 1.5, "x")
        self.assertEqual(code, 400)

    def test_conviction_not_a_number(self):
        body, code = views.validate_thesis("long", "high", "x")
        self.assertEqual((code, body["error"]), (400, "invalid_conviction"))

    def test_reason_required(self):
        body, code = views.validate_thesis("flat", 0.5, "   ")
        self.assertEqual((code, body["error"]), (400, "reason_required"))


if __name__ == "__main__":
    unittest.main()
