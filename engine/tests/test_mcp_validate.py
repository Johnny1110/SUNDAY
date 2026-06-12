"""sunday_mcp.validate — cross-field rules (PRD-9.3 §2). Pure functions.

The contract under test: every violation is reported in ONE pass (no
drip-feeding), and a clean input returns an empty list.
"""

from __future__ import annotations

import unittest

from sunday_mcp import validate


def _order(**over):
    """A valid market-buy baseline; tests break one rule at a time."""
    kw = dict(side="buy", type="market", qty=0.01, notional_usd=None, price=None,
              take_profit=70000.0, stop_loss=60000.0)
    kw.update(over)
    return validate.order_violations(**kw)


class OrderRulesTest(unittest.TestCase):
    def test_valid_market_and_limit(self):
        self.assertEqual(_order(), [])
        self.assertEqual(_order(type="limit", price=63000.0), [])
        self.assertEqual(_order(qty=None, notional_usd=150.0), [])
        self.assertEqual(  # sell mirror
            _order(side="sell", take_profit=60000.0, stop_loss=70000.0), [])

    def test_sizing_exactly_one(self):
        self.assertIn("give exactly one of qty / notional_usd",
                      _order(qty=None, notional_usd=None))
        self.assertIn("give exactly one of qty / notional_usd",
                      _order(qty=0.01, notional_usd=150.0))

    def test_limit_price_pairing(self):
        self.assertIn("limit order requires price", _order(type="limit"))
        self.assertIn("market order must not carry price (it fills at market)",
                      _order(price=63000.0))

    def test_tp_sl_direction(self):
        self.assertEqual(["for buy: take_profit must be above stop_loss"],
                         _order(take_profit=60000.0, stop_loss=70000.0))
        self.assertEqual(["for buy: take_profit must be above stop_loss"],
                         _order(take_profit=65000.0, stop_loss=65000.0))  # equal = no
        self.assertEqual(["for sell: take_profit must be below stop_loss"],
                         _order(side="sell"))  # baseline TP>SL is wrong for sell

    def test_direction_skipped_when_either_missing(self):
        # defensive totality for direct callers; the schema requires both
        self.assertEqual(_order(take_profit=None), [])
        self.assertEqual(_order(stop_loss=None), [])

    def test_all_violations_reported_at_once(self):
        out = _order(type="limit", qty=0.01, notional_usd=150.0, price=None,
                     take_profit=60000.0, stop_loss=70000.0)
        self.assertEqual(len(out), 3)
        self.assertIn("give exactly one of qty / notional_usd", out)
        self.assertIn("limit order requires price", out)
        self.assertIn("for buy: take_profit must be above stop_loss", out)


class ProtectionRulesTest(unittest.TestCase):
    def test_at_least_one_leg(self):
        out = validate.protection_violations(None, None)
        self.assertEqual(len(out), 1)
        self.assertIn("take_profit and/or stop_loss", out[0])

    def test_one_leg_is_enough(self):
        self.assertEqual(validate.protection_violations(70000.0, None), [])
        self.assertEqual(validate.protection_violations(None, 60000.0), [])
        self.assertEqual(validate.protection_violations(70000.0, 60000.0), [])


class LeverageMarginRulesTest(unittest.TestCase):
    def test_at_least_one_setting(self):
        self.assertEqual(["provide leverage and/or margin_mode"],
                         validate.leverage_margin_violations(None, None))
        self.assertEqual(validate.leverage_margin_violations(5, None), [])
        self.assertEqual(validate.leverage_margin_violations(None, "isolated"), [])


class AlertsRulesTest(unittest.TestCase):
    def test_delete_requires_id(self):
        out = validate.alerts_violations("delete", None)
        self.assertEqual(len(out), 1)
        self.assertIn("action=delete requires id", out[0])

    def test_clean_actions(self):
        self.assertEqual(validate.alerts_violations("delete", 7), [])
        self.assertEqual(validate.alerts_violations("list", None), [])
        self.assertEqual(validate.alerts_violations("list", 7), [])  # id ignored


if __name__ == "__main__":
    unittest.main()
