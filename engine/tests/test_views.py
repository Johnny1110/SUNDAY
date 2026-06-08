"""Unit tests for the milestone-3 HTTP logic (panel + defensive lever)."""

import unittest
from datetime import datetime, timezone

from sunday import views
from sunday.market import Candles


def candles(closes):
    n = len(closes)
    return Candles([i for i in range(n)], [closes[0]] + closes[:-1],
                   [c + 0.5 for c in closes], [c - 0.5 for c in closes],
                   list(map(float, closes)), [1.0] * n)


FIXED = datetime(2026, 6, 8, 11, 0, tzinfo=timezone.utc)


class TestPanels(unittest.TestCase):
    def test_signals_view_shape(self):
        v = views.signals_view("BTCUSDT", candles([float(i) for i in range(1, 80)]), "momentum", as_of=FIXED)
        self.assertEqual(v["symbol"], "BTCUSDT")
        self.assertEqual(v["active"], "momentum")
        self.assertEqual(v["as_of_ts"], FIXED.isoformat())
        self.assertIn("label", v["regime"])
        self.assertEqual([x["strategy"] for x in v["votes"]], ["momentum", "mean_reversion"])

    def test_status_view_adds_legibility_fields(self):
        state = {"alive": True, "strategy": "momentum", "last_lever": {"by": "friday", "what": "strategy"}}
        out = views.status_view(state, candles([float(i) for i in range(1, 80)]), as_of=FIXED)
        self.assertEqual(out["as_of_ts"], FIXED.isoformat())
        self.assertEqual(out["last_lever"]["by"], "friday")
        self.assertEqual(len(out["votes"]), 2)        # votes summary present

    def test_status_view_without_candles_has_no_votes(self):
        out = views.status_view({"alive": True}, as_of=FIXED)
        self.assertNotIn("votes", out)


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


class TestApplyEnvelope(unittest.TestCase):
    def cur(self):
        return {"max_position_usd": 2000.0, "max_total_exposure_usd": 4000.0,
                "max_leverage": 3.0, "max_drawdown_pct": 5.0, "stop_pct": 2.0}

    def test_valid_partial_update(self):
        body, code = views.apply_envelope(self.cur(), {"max_leverage": 2}, "de-risk")
        self.assertEqual(code, 200)
        self.assertTrue(body["applied"])
        self.assertEqual(body["resulting_status"]["envelope"]["max_leverage"], 2.0)
        self.assertEqual(body["resulting_status"]["envelope"]["max_position_usd"], 2000.0)  # untouched

    def test_reason_required(self):
        body, code = views.apply_envelope(self.cur(), {"max_leverage": 2}, "  ")
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "reason_required")

    def test_negative_rejected(self):
        body, code = views.apply_envelope(self.cur(), {"max_position_usd": -1}, "x")
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "invalid_envelope")

    def test_non_number_rejected(self):
        body, code = views.apply_envelope(self.cur(), {"max_leverage": "high"}, "x")
        self.assertEqual(code, 400)

    def test_bool_rejected(self):
        body, code = views.apply_envelope(self.cur(), {"max_leverage": True}, "x")
        self.assertEqual(code, 400)

    def test_unknown_field_ignored(self):
        body, code = views.apply_envelope(self.cur(), {"max_yolo": 9}, "x")
        self.assertEqual(code, 200)
        self.assertFalse(body["applied"])           # nothing real changed

    def test_idempotent_same_value(self):
        body, code = views.apply_envelope(self.cur(), {"max_leverage": 3.0}, "no-op")
        self.assertEqual(code, 200)
        self.assertFalse(body["applied"])


if __name__ == "__main__":
    unittest.main()
