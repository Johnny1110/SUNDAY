"""Unit tests for the decision-support advisor (the agent's money-making tool)."""

import unittest

from sunday import advisor
from sunday.market import Candles


def candles(closes):
    n = len(closes)
    return Candles([i for i in range(n)], [closes[0]] + closes[:-1],
                   [c + 0.5 for c in closes], [c - 0.5 for c in closes],
                   list(map(float, closes)), [1.0] * n)


class TestFunding(unittest.TestCase):
    def test_high_positive_favors_short(self):
        f = advisor.funding_context(0.001)
        self.assertEqual(f["bias"], "favors_short")
        self.assertGreater(f["annualized_pct"], 0)

    def test_high_negative_favors_long(self):
        self.assertEqual(advisor.funding_context(-0.001)["bias"], "favors_long")

    def test_small_is_neutral(self):
        self.assertEqual(advisor.funding_context(0.00001)["bias"], "neutral")

    def test_none_unknown(self):
        self.assertEqual(advisor.funding_context(None)["bias"], "unknown")


class TestRecommend(unittest.TestCase):
    def votes(self, mom, mr):
        return [{"strategy": "momentum", "vote": mom, "rationale": "m"},
                {"strategy": "mean_reversion", "vote": mr, "rationale": "r"}]

    def test_trending_picks_momentum(self):
        rec = advisor.recommend("trending", self.votes("long", "neutral"), advisor.funding_context(0))
        self.assertEqual(rec["strategy"], "momentum")
        self.assertEqual(rec["direction"], "long")

    def test_ranging_touched_picks_mean_reversion(self):
        rec = advisor.recommend("ranging", self.votes("neutral", "long"), advisor.funding_context(0))
        self.assertEqual(rec["strategy"], "mean_reversion")

    def test_volatile_picks_flat(self):
        self.assertEqual(advisor.recommend("volatile", self.votes("long", "short"), advisor.funding_context(0))["strategy"], "flat")

    def test_ranging_untouched_flat(self):
        self.assertEqual(advisor.recommend("ranging", self.votes("neutral", "neutral"), advisor.funding_context(0))["strategy"], "flat")

    def test_funding_caveat_when_fighting_tilt(self):
        rec = advisor.recommend("trending", self.votes("long", "neutral"), advisor.funding_context(0.001))
        self.assertIn("funding_caveat", rec)


class TestAdvise(unittest.TestCase):
    def test_panel_shape(self):
        p = advisor.advise(candles([float(i) for i in range(1, 80)]), 0.0001, "momentum", symbol="BTCUSDT")
        for k in ("symbol", "active", "regime", "funding", "votes", "recommendation"):
            self.assertIn(k, p)
        self.assertEqual(p["symbol"], "BTCUSDT")
        self.assertEqual([v["strategy"] for v in p["votes"]], ["momentum", "mean_reversion"])


if __name__ == "__main__":
    unittest.main()
