"""Unit tests for per-switch outcome attribution."""

import unittest

from sunday import attribution
from sunday.attribution import attribute


def switch(strategy, set_at, reason="", set_by="friday", symbol="BTCUSDT"):
    return {"symbol": symbol, "strategy": strategy, "set_by": set_by, "reason": reason, "set_at": set_at}


def pos(strategy, opened_at, pnl, qty=1.0, entry=1000.0, closed_at=1):
    return {"symbol": "BTCUSDT", "strategy": strategy, "qty": qty, "entry_price": entry,
            "realized_pnl": pnl, "opened_at": opened_at, "closed_at": closed_at}


class TestAttribute(unittest.TestCase):
    def test_empty_switches(self):
        self.assertEqual(attribute([], [pos("momentum", 5, 10)]), [])

    def test_two_episodes_split_by_switch_time(self):
        switches = [switch("momentum", 100), switch("mean_reversion", 200)]
        positions = [
            pos("momentum", 110, 50.0),         # episode 1
            pos("momentum", 150, -20.0),        # episode 1
            pos("mean_reversion", 210, 30.0),   # episode 2
        ]
        eps = attribute(switches, positions)
        self.assertEqual(len(eps), 2)
        self.assertAlmostEqual(eps[0].realized_pnl, 30.0)   # 50 - 20
        self.assertEqual(eps[0].trades, 2)
        self.assertAlmostEqual(eps[0].win_rate, 0.5)
        self.assertAlmostEqual(eps[1].realized_pnl, 30.0)
        self.assertEqual(eps[1].ended_at, None)             # last episode is open-ended

    def test_open_position_counted_not_realized(self):
        eps = attribute([switch("momentum", 100)],
                        [pos("momentum", 110, 0.0, closed_at=None), pos("momentum", 120, 40.0)])
        self.assertEqual(eps[0].open_trades, 1)
        self.assertEqual(eps[0].trades, 1)            # only the closed one
        self.assertAlmostEqual(eps[0].realized_pnl, 40.0)

    def test_return_pct_on_deployed_capital(self):
        # one closed trade: pnl 100 on 1.0 * 1000 = 1000 deployed → 10%
        eps = attribute([switch("momentum", 100)], [pos("momentum", 110, 100.0, qty=1.0, entry=1000.0)])
        self.assertAlmostEqual(eps[0].return_pct, 10.0)
        self.assertAlmostEqual(eps[0].deployed_usd, 1000.0)

    def test_position_before_first_switch_ignored(self):
        eps = attribute([switch("momentum", 100)], [pos("momentum", 50, 999.0)])
        self.assertEqual(eps[0].trades, 0)
        self.assertEqual(eps[0].realized_pnl, 0.0)

    def test_as_dict_shape(self):
        eps = attribute([switch("momentum", 100, reason="trend up")], [pos("momentum", 110, 5.0)])
        d = eps[0].as_dict()
        for key in ("symbol", "strategy", "set_by", "reason", "set_at", "ended_at",
                    "realized_pnl", "trades", "win_rate", "return_pct"):
            self.assertIn(key, d)


if __name__ == "__main__":
    unittest.main()
