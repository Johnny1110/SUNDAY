"""sunday_mcp.shaping — number rendering, markets/positions rows, protection
verdict three-plus-one states, memo clipping. Pure functions, no network."""

from __future__ import annotations

import unittest

from sunday_mcp import shaping
from sunday_mcp.client import Reply
from sunday_mcp import errors


def _envelope(items, page=1, page_size=10, total=None, has_more=False):
    return {"items": items, "page": page, "page_size": page_size,
            "total": len(items) if total is None else total, "has_more": has_more}


def _pos(**over):
    row = {
        "symbol": "BTCUSDT", "side": "long", "qty": 0.02, "entry": 67000.0,
        "mark": 68200.5, "leverage": 5, "margin_mode": "isolated",
        "notional": 1364.01, "unrealized_pnl": 24.0, "roi_pct": 8.8,
        "liquidation_price": 54000.0, "liq_distance_pct": -20.82,
        "protection": {"take_profit": True, "stop_loss": True, "sl_qty_covers": True},
        "memo": "4h 突破壓力 + 資金費轉負，順勢做多",
    }
    row.update(over)
    return row


class FmtTest(unittest.TestCase):
    def test_fmt_price(self):
        self.assertEqual(shaping.fmt_price(67000.0), "67000")
        self.assertEqual(shaping.fmt_price(67000.5), "67000.5")
        self.assertEqual(shaping.fmt_price(0.00001234), "0.00001234")  # no sci notation
        self.assertEqual(shaping.fmt_price(0), "0")
        self.assertEqual(shaping.fmt_price(None), "?")

    def test_fmt_pct(self):
        self.assertEqual(shaping.fmt_pct(1.234), "+1.23%")
        self.assertEqual(shaping.fmt_pct(-0.5), "-0.50%")
        self.assertEqual(shaping.fmt_pct(None), "?")

    def test_fmt_usd(self):
        self.assertEqual(shaping.fmt_usd(950), "950")
        self.assertEqual(shaping.fmt_usd(1234), "1.23k")
        self.assertEqual(shaping.fmt_usd(5_600_000), "5.60M")
        self.assertEqual(shaping.fmt_usd(1_523_000_000), "1.52B")
        self.assertEqual(shaping.fmt_usd(None), "?")

    def test_clip(self):
        self.assertIsNone(shaping.clip(None))
        self.assertEqual(shaping.clip("short"), "short")
        self.assertEqual(shaping.clip("a\nb   c"), "a b c")  # one-line normalisation
        long = "x" * 80
        clipped = shaping.clip(long)
        self.assertEqual(len(clipped), shaping.MEMO_MAX)
        self.assertTrue(clipped.endswith("…"))


class MarketsTest(unittest.TestCase):
    def test_rows_and_tail(self):
        out = shaping.shape_markets(_envelope(
            [{"symbol": "BTCUSDT", "last": 67000.5, "change_pct": 1.234,
              "quote_volume": 1_523_000_000}],
            total=320, has_more=True))
        self.assertIn("BTCUSDT  67000.5  +1.23%  1.52B vol", out)
        self.assertIn("page 1 · total 320 · has_more: true", out)

    def test_empty(self):
        out = shaping.shape_markets(_envelope([]))
        self.assertIn("no markets matched", out)
        self.assertIn("has_more: false", out)


class PositionsTest(unittest.TestCase):
    def test_full_row(self):
        out = shaping.shape_positions(_envelope([_pos()]))
        self.assertIn("BTCUSDT long 0.02 @67000 mark 68200.5", out)
        self.assertIn("roi +8.80%", out)
        self.assertIn("5x isolated", out)
        self.assertIn("liq -20.82%", out)
        self.assertIn("TP✓ SL✓", out)
        self.assertIn("| 4h 突破壓力", out)

    def test_protection_states(self):
        self.assertEqual(shaping.protection_str(None), "TP? SL?(unknown)")
        self.assertEqual(
            shaping.protection_str({"take_profit": True, "stop_loss": False}),
            "TP✓ SL✗(naked)")
        self.assertEqual(
            shaping.protection_str(
                {"take_profit": False, "stop_loss": True, "sl_qty_covers": False}),
            "TP✗ SL△(partial)")
        self.assertEqual(
            shaping.protection_str(
                {"take_profit": True, "stop_loss": True, "sl_qty_covers": True}),
            "TP✓ SL✓")

    def test_cross_position_unknowns(self):
        out = shaping.shape_positions(_envelope(
            [_pos(margin_mode=None, leverage=None, liq_distance_pct=None,
                  protection=None, memo=None)]))
        self.assertIn("?x ?", out)
        self.assertIn("liq ?", out)
        self.assertIn("TP? SL?(unknown)", out)
        self.assertNotIn("|", out)  # no memo → no memo separator

    def test_empty_book(self):
        self.assertEqual(shaping.shape_positions(_envelope([])), "no open positions")

    def test_tail_only_when_more_pages(self):
        one = shaping.shape_positions(_envelope([_pos()]))
        self.assertNotIn("has_more", one)
        more = shaping.shape_positions(_envelope([_pos()], total=60, has_more=True))
        self.assertIn("has_more: true", more)


class ErrorsTest(unittest.TestCase):
    def test_upstream_error_passthrough(self):
        r = Reply(status=400, json={"detail": "x"}, text='{"detail": "x"}\n')
        self.assertTrue(errors.is_error(r))
        self.assertEqual(errors.upstream_error_text(r), '[sunday 400] {"detail": "x"}')

    def test_ok_is_not_error(self):
        self.assertFalse(errors.is_error(Reply(status=200, json={}, text="{}")))


# ── Phase 2 read-only shapers ─────────────────────────────────────────────────

class MarketDetailTest(unittest.TestCase):
    PAYLOAD = {
        "symbol": "BTCUSDT",
        "ticker": {"last": 67000.5, "bid": 67000.0, "ask": 67001.0, "high": 68000.0,
                   "low": 65000.0, "change_pct": 1.234, "quote_volume": 1_523_000_000},
        "info": {"precision": {"price": 0.1, "amount": 0.001}, "contract_size": 1,
                 "limits": {"amount": {"min": 0.001, "max": 500},
                            "cost": {"min": 100, "max": None}},
                 "max_leverage": 125, "maker": 0.0002, "taker": 0.0004, "active": True},
    }

    def test_blocks(self):
        out = shaping.shape_market_detail(self.PAYLOAD)
        self.assertIn("BTCUSDT  last 67000.5", out)
        self.assertIn("range 65000–68000", out)
        self.assertIn("precision: price 0.1 · qty 0.001", out)
        self.assertIn("limits: qty 0.001–500 · notional ≥ 100", out)
        self.assertIn("max leverage 125x", out)
        self.assertIn("fees: maker 0.02% · taker 0.04% · active: true", out)

    def test_missing_info_is_defensive(self):
        out = shaping.shape_market_detail({"symbol": "X"})
        self.assertIn("X  last ?", out)
        self.assertIn("max leverage ?x", out)


class KlinesTest(unittest.TestCase):
    def test_header_and_csv(self):
        out = shaping.shape_klines({
            "symbol": "BTCUSDT", "interval": "1h", "count": 2,
            "ohlcv": [[1718060400000, 67000.0, 67100.0, 66900.0, 67050.5, 1234.56],
                      [1718064000000, 67050.5, 67200.0, 67000.0, 67150.0, 980.1]]})
        lines = out.splitlines()
        self.assertEqual(lines[0], "BTCUSDT 1h · 2 bars · ts,open,high,low,close,volume")
        self.assertEqual(lines[1], "1718060400000,67000,67100,66900,67050.5,1234.56")

    def test_stale_banner_first(self):
        out = shaping.shape_klines({"symbol": "X", "interval": "1h", "count": 0,
                                    "ohlcv": [], "stale": True, "stale_age_s": 42.0})
        self.assertTrue(out.startswith("⚠ stale (age 42.0s)"))

    def test_budget_worst_case(self):
        # 500 bars × max-width floats must stay under the 60k design budget (S3)
        row = [1718060400000] + [123456.7890123456] * 5
        out = shaping.shape_klines({"symbol": "BTCUSDT", "interval": "1m",
                                    "count": 500, "ohlcv": [row] * 500})
        self.assertLess(len(out), 60_000)


class IndicatorsTest(unittest.TestCase):
    PAYLOAD = {
        "symbol": "BTCUSDT", "interval": "1h", "as_of": 1718064000000,
        "last_close": 67050.5,
        "indicators": {
            "rsi": 56.78999999999999,
            "ema": {"ema20": 66980.12, "ema50": 66800.45},
            "macd": {"macd": 120.5, "signal": 95.2, "hist": 25.3},
            "bollinger": {"mid": 66900.0, "upper": 67400.0, "lower": 66400.0,
                          "sd": 250.0, "z": 0.602},
            "adx": 18.7, "atr": 450.23,
        },
    }

    def test_panel(self):
        out = shaping.shape_indicators(self.PAYLOAD)
        self.assertIn("BTCUSDT 1h · as_of 1718064000000 · last_close 67050.5", out)
        self.assertIn("rsi 56.79", out)
        self.assertIn("ema: ema20 66980.12 · ema50 66800.45", out)
        self.assertIn("bollinger: mid 66900 · upper 67400 · lower 66400", out)
        self.assertIn("atr 450.23", out)
        self.assertLess(len(out), 2_000)  # panel is scalars — tiny by design

    def test_stale_and_empty(self):
        out = shaping.shape_indicators({"symbol": "X", "interval": "1h",
                                        "indicators": {}, "stale": True})
        self.assertTrue(out.startswith("⚠ stale"))
        self.assertIn("no indicators computed", out)


class FundingTest(unittest.TestCase):
    def test_current(self):
        out = shaping.shape_funding({"symbol": "BTCUSDT", "rate": 0.0001,
                                     "mark": 67000.5, "index": 66998.2,
                                     "next_funding_ts": 1718064000000,
                                     "interval_hours": 8})
        self.assertEqual(out, "BTCUSDT funding +0.01% · mark 67000.5 · index 66998.2"
                              " · next_ts 1718064000000 · every 8h")

    def test_negative_rate_keeps_sign(self):
        out = shaping.shape_funding({"symbol": "X", "rate": -0.000125})
        self.assertIn("funding -0.0125%", out)

    def test_history(self):
        out = shaping.shape_funding_history(_envelope(
            [{"ts": 1718064000000, "rate": 0.0001}], total=300, has_more=True))
        self.assertIn("1718064000000  +0.01%", out)
        self.assertIn("has_more: true", out)


class IndicesTest(unittest.TestCase):
    def test_snapshot_lines(self):
        out = shaping.shape_indices({"items": [
            {"key": "fear-greed", "label": "Fear & Greed", "available": True,
             "value": 72, "classification": "Greed", "stale": False},
            {"key": "vix", "label": "VIX", "available": True, "value": 14.5,
             "change_pct": -3.1, "stale": True},
            {"key": "gold", "label": "Gold", "available": False},
        ]})
        self.assertIn("Fear & Greed: 72 · Greed", out)
        self.assertIn("VIX: 14.5 · -3.10% · ⚠ stale", out)
        self.assertIn("Gold: unavailable", out)

    def test_one_index_with_asof(self):
        out = shaping.shape_index({"key": "btc-dominance", "label": "BTC dominance",
                                   "available": True, "value": 54.2, "unit": "%",
                                   "as_of": 1718064000000})
        self.assertIn("BTC dominance: 54.2%", out)
        self.assertIn("as_of 1718064000000", out)


class BalancePnlTest(unittest.TestCase):
    def test_balance_line(self):
        out = shaping.shape_balance({"equity": 10234.56, "wallet": 10100.0,
                                     "free": 8200.5, "used": 1900.0,
                                     "unrealized_pnl": 134.56})
        self.assertEqual(out, "equity 10234.56 · wallet 10100 · free 8200.5"
                              " · used 1900 · unrealized +134.56")

    def test_pnl_drawdown_merged(self):
        out = shaping.shape_pnl_drawdown(
            {"equity": 10234.56, "unrealized_pnl": 134.56, "total_notional": 1520.0,
             "exposure_pct": 14.85,
             "positions": [{"symbol": "BTCUSDT", "side": "long", "notional": 1520.0,
                            "unrealized_pnl": 134.56, "roi_pct": 8.8}]},
            {"drawdown_pct": 2.53, "high_water": 10500.0,
             "high_water_ts": 1718000000000, "samples": 1234})
        self.assertIn("exposure 14.85%", out)        # unsigned — it's a magnitude
        self.assertIn("BTCUSDT long notional 1.52k upnl +134.56 roi +8.80%", out)
        self.assertIn("drawdown 2.53% · high_water 10500", out)
        self.assertNotIn("low confidence", out)

    def test_short_history_flag(self):
        out = shaping.shape_pnl_drawdown(None, {"drawdown_pct": 0.0, "high_water": 100.0,
                                                "high_water_ts": None, "samples": 3},
                                         pnl_error="[sunday 502] exchange error")
        self.assertIn("pnl: [sunday 502] exchange error", out)  # partial failure shown
        self.assertIn("samples 3 (short history — low confidence)", out)

    def test_budget_many_positions(self):
        rows = [{"symbol": "BTCUSDT", "side": "short", "notional": 123456.789,
                 "unrealized_pnl": -1234.5678, "roi_pct": -12.34}] * 100
        out = shaping.shape_pnl_drawdown(
            {"equity": 1, "unrealized_pnl": 0, "total_notional": 1, "exposure_pct": 1,
             "positions": rows}, None, dd_error="x")
        self.assertLess(len(out), 60_000)


class OrdersTradesTest(unittest.TestCase):
    def _order(self, **over):
        row = {"id": "123456", "symbol": "BTCUSDT", "type": "limit", "side": "buy",
               "price": 67000.0, "amount": 0.02, "filled": 0.0, "remaining": 0.02,
               "status": "new", "reduce_only": False, "close_position": False,
               "trigger_price": None, "tp_sl": None, "algo": False,
               "leverage": 5, "ts": 1718064000000, "agent": "friday"}
        row.update(over)
        return row

    def test_limit_order_line(self):
        out = shaping.shape_orders(_envelope([self._order()]))
        self.assertIn("#123456 BTCUSDT buy limit @67000 qty 0.02 new · friday", out)
        self.assertIn("has_more: false", out)

    def test_algo_sl_leg_flags(self):
        out = shaping.shape_orders(_envelope([self._order(
            price=None, trigger_price=60000.0, type="stop_market",
            tp_sl="stop_loss", algo=True, reduce_only=True, agent=None)]))
        self.assertIn("trig 60000", out)
        self.assertIn("[SL algo RO]", out)
        self.assertIn("· agent:?", out)

    def test_trades_page_sum(self):
        out = shaping.shape_trades(_envelope([
            {"ts": 1718064000000, "side": "sell", "amount": 0.02, "price": 68000.0,
             "realized_pnl": 20.0, "fee": 0.54, "agent": "friday"},
            {"ts": 1718060400000, "side": "buy", "amount": 0.02, "price": 67000.0,
             "realized_pnl": -2.5, "fee": 0.53, "agent": None},
        ]))
        self.assertIn("pnl +20.00", out)
        self.assertIn("Σ realized (this page): +17.50", out)

    def test_budget_worst_pages(self):
        worst_order = self._order(id="9" * 20, price=123456.7890123456,
                                  amount=123456.7890123456, filled=123456.789,
                                  agent="a" * 32, tp_sl="take_profit", algo=True,
                                  reduce_only=True, close_position=True)
        out = shaping.shape_orders(_envelope([worst_order] * 30))
        self.assertLess(len(out), 60_000)
        worst_trade = {"ts": 1718064000000, "side": "sell",
                       "amount": 123456.7890123456, "price": 123456.7890123456,
                       "realized_pnl": -123456.78, "fee": 123.4567890123,
                       "agent": "a" * 32}
        out = shaping.shape_trades(_envelope([worst_trade] * 50))
        self.assertLess(len(out), 60_000)

    def test_empty_lists(self):
        self.assertIn("no orders", shaping.shape_orders(_envelope([])))
        self.assertIn("no trades", shaping.shape_trades(_envelope([])))


class ProtectionStatusTest(unittest.TestCase):
    LEG_TP = {"id": "111", "trigger_price": 75000.0, "status": "new", "ts": 2}
    LEG_SL = {"id": "222", "trigger_price": 60000.0, "status": "new", "ts": 1}

    def test_protected_position(self):
        out = shaping.shape_protection_status({
            "symbol": "BTCUSDT",
            "position": {"side": "long", "qty": 0.02, "entry": 67000.0,
                         "mark": 68200.5, "leverage": 5},
            "take_profit": self.LEG_TP, "stop_loss": self.LEG_SL,
            "tp_legs": 1, "sl_legs": 2, "sl_qty_covers": True})
        self.assertIn("BTCUSDT long 0.02 @67000 mark 68200.5 5x", out)
        self.assertIn("TP #111 trigger 75000 new (1 leg)", out)
        self.assertIn("SL #222 trigger 60000 new (2 legs) · covers qty: true", out)
        self.assertNotIn("ORPHAN", out)

    def test_orphan_legs_warning_first(self):
        out = shaping.shape_protection_status({
            "symbol": "BTCUSDT", "position": None,
            "take_profit": self.LEG_TP, "stop_loss": None,
            "tp_legs": 1, "sl_legs": 0, "sl_qty_covers": None})
        self.assertTrue(out.startswith("ORPHAN LEGS"))
        self.assertIn("TP #111", out)
        self.assertIn("SL: none (0 legs)", out)

    def test_flat_no_legs(self):
        out = shaping.shape_protection_status({
            "symbol": "ETHUSDT", "position": None, "take_profit": None,
            "stop_loss": None, "tp_legs": 0, "sl_legs": 0, "sl_qty_covers": None})
        self.assertEqual(out, "ETHUSDT: flat — no position, no trigger legs")


class FmtPhase2Test(unittest.TestCase):
    def test_fmt_frac_pct(self):
        self.assertEqual(shaping.fmt_frac_pct(0.0001, signed=True), "+0.01%")
        self.assertEqual(shaping.fmt_frac_pct(-0.000125, signed=True), "-0.0125%")
        self.assertEqual(shaping.fmt_frac_pct(0.0004), "0.04%")
        self.assertEqual(shaping.fmt_frac_pct(0), "0%")
        self.assertEqual(shaping.fmt_frac_pct(None), "?")

    def test_fmt_pct_plain_and_signed(self):
        self.assertEqual(shaping.fmt_pct_plain(2.5), "2.50%")
        self.assertEqual(shaping.fmt_pct_plain(None), "?")
        self.assertEqual(shaping.fmt_signed(-12.345), "-12.35")
        self.assertEqual(shaping.fmt_signed(None), "?")

    def test_stale_banner(self):
        self.assertIsNone(shaping.stale_banner({}))
        self.assertEqual(shaping.stale_banner({"stale": True}), "⚠ stale")
        self.assertEqual(shaping.stale_banner({"stale": True, "stale_age_s": 7.5}),
                         "⚠ stale (age 7.5s)")


if __name__ == "__main__":
    unittest.main()
