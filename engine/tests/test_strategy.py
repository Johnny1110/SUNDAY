"""Unit tests for the strategy engine + Candles."""

import unittest

from sunday import strategy as strat
from sunday.market import Candles


def candles(closes):
    """Build Candles from a close series (synthetic OHLV around each close)."""
    n = len(closes)
    opens = [closes[0]] + closes[:-1]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    vols = [1.0] * n
    times = [i * 3_600_000 for i in range(n)]
    return Candles(times, opens, highs, lows, list(map(float, closes)), vols)


class TestCandles(unittest.TestCase):
    def test_from_klines(self):
        raw = [[1000, "10", "12", "9", "11", "100", 1999], [2000, "11", "13", "10", "12", "120", 2999]]
        c = Candles.from_klines(raw)
        self.assertEqual(len(c), 2)
        self.assertEqual(c.closes, [11.0, 12.0])
        self.assertEqual(c.highs, [12.0, 13.0])
        self.assertEqual(c.last_close, 12.0)

    def test_to_rows_roundtrips_columns(self):
        c = candles([1, 2, 3])
        rows = c.to_rows()
        self.assertEqual(rows[0][0], 0)          # ts
        self.assertEqual(rows[-1][4], 3.0)       # close


class TestMomentum(unittest.TestCase):
    def test_uptrend_votes_long(self):
        v = strat.evaluate("momentum", candles([float(i) for i in range(1, 80)]))
        self.assertEqual(v.vote, "long")
        self.assertGreater(v.confidence, 0.0)
        self.assertIn("ema20", v.indicators)

    def test_downtrend_votes_short(self):
        v = strat.evaluate("momentum", candles([float(i) for i in range(80, 1, -1)]))
        self.assertEqual(v.vote, "short")

    def test_flat_tape_neutral(self):
        v = strat.evaluate("momentum", candles([100.0] * 80))
        self.assertEqual(v.vote, "neutral")

    def test_insufficient_data_neutral(self):
        v = strat.evaluate("momentum", candles([float(i) for i in range(1, 40)]))
        self.assertEqual(v.vote, "neutral")
        self.assertIn("資料不足", v.rationale)


class TestMeanReversion(unittest.TestCase):
    def test_oversold_votes_long(self):
        v = strat.evaluate("mean_reversion", candles([100.0] * 30 + [98, 96, 94, 92, 90]))
        self.assertEqual(v.vote, "long")
        self.assertLessEqual(v.indicators["bb_z"], -1.0)

    def test_overbought_votes_short(self):
        v = strat.evaluate("mean_reversion", candles([100.0] * 30 + [102, 104, 106, 108, 110]))
        self.assertEqual(v.vote, "short")

    def test_quiet_tape_neutral(self):
        v = strat.evaluate("mean_reversion", candles([100.0] * 40))
        self.assertEqual(v.vote, "neutral")


class TestFlatAndApi(unittest.TestCase):
    def test_flat_is_deliberate_neutral(self):
        v = strat.evaluate("flat", candles([100.0] * 80))
        self.assertEqual(v.vote, "neutral")
        self.assertEqual(v.confidence, 1.0)

    def test_unknown_strategy_raises(self):
        with self.assertRaises(ValueError):
            strat.evaluate("rsi_scalper", candles([1.0] * 80))

    def test_vote_all_is_the_two_candidates(self):
        votes = strat.vote_all(candles([float(i) for i in range(1, 80)]))
        self.assertEqual([v.strategy for v in votes], ["momentum", "mean_reversion"])

    def test_vote_as_dict_rounds(self):
        d = strat.evaluate("momentum", candles([float(i) for i in range(1, 80)])).as_dict()
        self.assertEqual(set(d), {"strategy", "vote", "confidence", "indicators", "rationale"})


if __name__ == "__main__":
    unittest.main()
