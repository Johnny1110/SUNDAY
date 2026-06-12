"""Unit tests for the pure risk-view math (protection / exposure / drawdown)."""

import unittest

from sunday import protection as P


class TestClassifyLeg(unittest.TestCase):
    def test_take_profit_variants(self):
        self.assertEqual(P.classify_leg("TAKE_PROFIT"), "take_profit")
        self.assertEqual(P.classify_leg("TAKE_PROFIT_MARKET"), "take_profit")

    def test_stop_variants(self):
        self.assertEqual(P.classify_leg("STOP"), "stop_loss")
        self.assertEqual(P.classify_leg("STOP_MARKET"), "stop_loss")
        self.assertEqual(P.classify_leg("TRAILING_STOP_MARKET"), "stop_loss")

    def test_plain_orders_are_not_legs(self):
        self.assertIsNone(P.classify_leg("LIMIT"))
        self.assertIsNone(P.classify_leg("MARKET"))
        self.assertIsNone(P.classify_leg(None))


class TestImmediateTrigger(unittest.TestCase):
    """BUG-01/BUG-04 guard: a trigger already in its fire zone executes instantly
    (the Algo Service accepts it instead of rejecting -2021)."""

    def test_long_stop_loss(self):
        self.assertFalse(P.immediate_trigger("sell", False, 62500.0, 63150.0))  # below mark: safe
        self.assertTrue(P.immediate_trigger("sell", False, 63500.0, 63150.0))   # above mark: fires

    def test_long_take_profit(self):
        self.assertFalse(P.immediate_trigger("sell", True, 64000.0, 63150.0))
        self.assertTrue(P.immediate_trigger("sell", True, 63000.0, 63150.0))

    def test_short_stop_loss(self):
        self.assertFalse(P.immediate_trigger("buy", False, 64000.0, 63150.0))
        self.assertTrue(P.immediate_trigger("buy", False, 62000.0, 63150.0))

    def test_short_take_profit(self):
        self.assertFalse(P.immediate_trigger("buy", True, 62000.0, 63150.0))
        self.assertTrue(P.immediate_trigger("buy", True, 64000.0, 63150.0))

    def test_trigger_at_mark_is_in_the_fire_zone(self):
        # Binance trigger conditions are inclusive (≤ / ≥) — equality fires.
        self.assertTrue(P.immediate_trigger("sell", False, 63150.0, 63150.0))
        self.assertTrue(P.immediate_trigger("sell", True, 63150.0, 63150.0))

    def test_unjudgeable_inputs_return_none(self):
        self.assertIsNone(P.immediate_trigger("sell", False, None, 100.0))
        self.assertIsNone(P.immediate_trigger("sell", False, 90.0, None))
        self.assertIsNone(P.immediate_trigger(None, False, 90.0, 100.0))


class TestProtection(unittest.TestCase):
    def test_fully_protected(self):
        legs = [{"tp_sl": "take_profit", "amount": 1.0, "close_position": False},
                {"tp_sl": "stop_loss", "amount": 1.0, "close_position": False}]
        self.assertEqual(P.protection(1.0, legs),
                         {"take_profit": True, "stop_loss": True, "sl_qty_covers": True})

    def test_naked_position(self):
        self.assertEqual(P.protection(1.0, []),
                         {"take_profit": False, "stop_loss": False, "sl_qty_covers": False})

    def test_partial_stop_does_not_cover(self):
        legs = [{"tp_sl": "stop_loss", "amount": 0.4, "close_position": False}]
        got = P.protection(1.0, legs)
        self.assertTrue(got["stop_loss"])
        self.assertFalse(got["sl_qty_covers"])

    def test_close_position_leg_covers_with_zero_qty(self):
        # Binance closePosition=true STOP_MARKET orders carry origQty 0.
        legs = [{"tp_sl": "stop_loss", "amount": 0.0, "close_position": True}]
        self.assertTrue(P.protection(5.0, legs)["sl_qty_covers"])

    def test_split_stops_sum_to_cover(self):
        legs = [{"tp_sl": "stop_loss", "amount": 0.6, "close_position": False},
                {"tp_sl": "stop_loss", "amount": 0.4, "close_position": False}]
        self.assertTrue(P.protection(1.0, legs)["sl_qty_covers"])

    def test_tp_only_is_not_a_stop(self):
        legs = [{"tp_sl": "take_profit", "amount": 1.0, "close_position": False}]
        got = P.protection(1.0, legs)
        self.assertTrue(got["take_profit"])
        self.assertFalse(got["stop_loss"])
        self.assertFalse(got["sl_qty_covers"])


class TestLiqDistance(unittest.TestCase):
    def test_distance_pct(self):
        self.assertEqual(P.liq_distance_pct(100.0, 90.0), 10.0)
        self.assertEqual(P.liq_distance_pct(100.0, 110.0), 10.0)  # short side

    def test_unknown_when_missing(self):
        self.assertIsNone(P.liq_distance_pct(None, 90.0))
        self.assertIsNone(P.liq_distance_pct(100.0, None))
        self.assertIsNone(P.liq_distance_pct(0.0, 90.0))


class TestExposure(unittest.TestCase):
    def test_aggregates(self):
        rows = [{"notional": 300.0}, {"notional": 200.0}]
        self.assertEqual(P.exposure(rows, 1000.0),
                         {"total_notional": 500.0, "exposure_pct": 50.0})

    def test_no_equity_means_no_pct(self):
        self.assertEqual(P.exposure([{"notional": 100.0}], None),
                         {"total_notional": 100.0, "exposure_pct": None})

    def test_empty_book(self):
        self.assertEqual(P.exposure([], 1000.0),
                         {"total_notional": 0.0, "exposure_pct": 0.0})


class TestProtectionDetail(unittest.TestCase):
    """Per-symbol protection view for GET /api/perp/protection (PRD-003 §2b)."""

    @staticmethod
    def _leg(kind, id_, trigger, ts, amount=1.0, close_position=False):
        return {"tp_sl": kind, "id": id_, "trigger_price": trigger, "status": "new",
                "amount": amount, "close_position": close_position, "ts": ts}

    def test_one_leg_each(self):
        legs = [self._leg("take_profit", "11", 750.0, 100),
                self._leg("stop_loss", "22", 650.0, 100)]
        got = P.protection_detail(1.0, legs)
        self.assertEqual(got["take_profit"]["id"], "11")
        self.assertEqual(got["stop_loss"]["id"], "22")
        self.assertEqual((got["tp_legs"], got["sl_legs"]), (1, 1))
        self.assertTrue(got["sl_qty_covers"])

    def test_no_legs(self):
        got = P.protection_detail(1.0, [])
        self.assertIsNone(got["take_profit"])
        self.assertIsNone(got["stop_loss"])
        self.assertEqual((got["tp_legs"], got["sl_legs"]), (0, 0))
        self.assertFalse(got["sl_qty_covers"])

    def test_primary_is_newest_and_ladder_counted(self):
        legs = [self._leg("stop_loss", "1", 660.0, 100, amount=0.5),
                self._leg("stop_loss", "2", 650.0, 200, amount=0.5)]
        got = P.protection_detail(1.0, legs)
        self.assertEqual(got["stop_loss"]["id"], "2")   # newest wins
        self.assertEqual(got["sl_legs"], 2)
        self.assertTrue(got["sl_qty_covers"])           # 0.5 + 0.5 covers 1.0

    def test_partial_stop_flagged(self):
        legs = [self._leg("stop_loss", "1", 650.0, 100, amount=0.4)]
        self.assertFalse(P.protection_detail(1.0, legs)["sl_qty_covers"])


class TestDrawdown(unittest.TestCase):
    def test_drawdown_from_hwm(self):
        self.assertEqual(P.drawdown_pct(900.0, 1000.0), 10.0)

    def test_new_high_is_zero_not_negative(self):
        self.assertEqual(P.drawdown_pct(1100.0, 1000.0), 0.0)

    def test_unknown_without_hwm(self):
        self.assertIsNone(P.drawdown_pct(900.0, None))
        self.assertIsNone(P.drawdown_pct(None, 1000.0))


if __name__ == "__main__":
    unittest.main()
