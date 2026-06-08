"""Unit tests for the deterministic risk circuit breakers (the safety fuse)."""

import unittest

from sunday import risk
from sunday.risk import Decision, Envelope, OrderProposal, RiskContext

ENV = risk.DEFAULT_ENVELOPE  # 2000 / 4000 / 3x / 5% / stop 2%


def order(qty, price=1000.0, has_stop=True, is_entry=True, side="buy"):
    return OrderProposal("BTCUSDT", side, qty, price, has_stop=has_stop, is_entry=is_entry)


class TestCheckOrder(unittest.TestCase):
    def test_within_envelope_allowed(self):
        d = risk.check_order(order(1.0), RiskContext(equity=5000, current_exposure_usd=0))
        self.assertTrue(d.allowed)

    def test_over_single_position_cap_rejected(self):
        # notional 2500 > max_position_usd 2000
        d = risk.check_order(order(2.5), RiskContext(equity=10000))
        self.assertFalse(d.allowed)
        self.assertIn("size_cap", d.violations)
        self.assertEqual(d.type, "size_cap")

    def test_over_total_exposure_rejected(self):
        # notional 1500 + existing 3000 = 4500 > 4000
        d = risk.check_order(order(1.5), RiskContext(equity=10000, current_exposure_usd=3000))
        self.assertFalse(d.allowed)
        self.assertIn("exposure_cap", d.violations)

    def test_over_leverage_rejected(self):
        # total_after 1500 / equity 400 = 3.75x > 3x
        d = risk.check_order(order(1.5), RiskContext(equity=400, current_exposure_usd=0))
        self.assertFalse(d.allowed)
        self.assertIn("leverage_cap", d.violations)

    def test_entry_without_stop_rejected(self):
        d = risk.check_order(order(1.0, has_stop=False), RiskContext(equity=5000))
        self.assertFalse(d.allowed)
        self.assertIn("no_stop", d.violations)

    def test_reduce_order_always_allowed_even_if_huge(self):
        # closing de-risks → never blocked, stop not required
        d = risk.check_order(order(99.0, has_stop=False, is_entry=False),
                             RiskContext(equity=10, current_exposure_usd=999))
        self.assertTrue(d.allowed)

    def test_multiple_violations_listed(self):
        d = risk.check_order(order(5.0, has_stop=False), RiskContext(equity=100))
        self.assertFalse(d.allowed)
        self.assertIn("size_cap", d.violations)
        self.assertIn("no_stop", d.violations)


class TestDrawdown(unittest.TestCase):
    def test_no_breach_below_limit(self):
        d = risk.check_drawdown(equity=9600, peak_equity=10000, env=ENV)  # 4% < 5%
        self.assertFalse(d.breached)
        self.assertAlmostEqual(d.drawdown_pct, 4.0)

    def test_breach_at_limit_triggers_flatten(self):
        d = risk.check_drawdown(equity=9500, peak_equity=10000, env=ENV)  # exactly 5%
        self.assertTrue(d.breached)
        self.assertEqual(d.action, "flatten_and_lock")

    def test_drawdown_zero_at_or_above_peak(self):
        self.assertEqual(risk.drawdown_pct(10001, 10000), 0.0)


if __name__ == "__main__":
    unittest.main()
