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
        self.assertIn("bid 67000  ask 67001", out)
        self.assertIn("range 65000–68000", out)
        self.assertIn("precision: price 0.1 · qty 0.001", out)
        self.assertIn("limits: qty 0.001–500 · notional ≥ 100", out)
        self.assertIn("max leverage 125x", out)
        self.assertIn("fees: maker 0.02% · taker 0.04% · active: true", out)

    def test_no_bid_ask_upstream_omits_pair(self):
        # the REAL binanceusdm shape: the 24h-ticker source has no bid/ask —
        # the pair must vanish, not render as a permanent "bid ? ask ?"
        payload = {**self.PAYLOAD,
                   "ticker": {**self.PAYLOAD["ticker"], "bid": None, "ask": None}}
        out = shaping.shape_market_detail(payload)
        self.assertNotIn("bid", out)
        self.assertNotIn("ask", out)
        self.assertIn("BTCUSDT  last 67000.5  24h +1.23%", out)

    def test_missing_info_is_defensive(self):
        out = shaping.shape_market_detail({"symbol": "X"})
        self.assertIn("X  last ?", out)
        self.assertNotIn("bid", out)
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

    def test_empty(self):
        self.assertEqual(shaping.shape_indices({"items": []}), "no indices available")


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

    def test_flat_account(self):
        out = shaping.shape_pnl_drawdown(
            {"equity": 10000.0, "unrealized_pnl": 0.0, "total_notional": 0.0,
             "exposure_pct": 0.0, "positions": []},
            {"drawdown_pct": 0.0, "high_water": 10000.0,
             "high_water_ts": 1718000000000, "samples": 1000})
        self.assertIn("no open positions", out)

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


class BudgetTest(unittest.TestCase):
    """Worst-LEGAL-input budget locks — every Phase 2 tool stays < 60k chars (S3).

    klines×500 / orders×30 / trades×50 / pnl_drawdown live in their own classes
    above; this class covers the remaining tools so each one has a budget test.
    Row counts mirror the schema page-size caps in server.py — raising a cap
    there without growing the matching case here must fail review.
    """

    BUDGET = 60_000
    W = 123456.7890123456  # widest float fmt_price will render
    SYM = "1000000BABYDOGEUSDT"  # longest real USDⓈ-M symbol shape

    def test_markets_page(self):
        row = {"symbol": self.SYM, "last": self.W, "change_pct": -123.45,
               "quote_volume": 987_654_321_098.7}
        out = shaping.shape_markets(_envelope([row] * 20, total=400, has_more=True))
        self.assertLess(len(out), self.BUDGET)

    def test_market_detail(self):
        out = shaping.shape_market_detail({
            "symbol": self.SYM,
            "ticker": {"last": self.W, "bid": self.W, "ask": self.W, "high": self.W,
                       "low": self.W, "change_pct": -123.45,
                       "quote_volume": 987_654_321_098.7},
            "info": {"precision": {"price": 0.0000001, "amount": 0.0000001},
                     "contract_size": self.W,
                     "limits": {"amount": {"min": 0.0000001, "max": self.W},
                                "cost": {"min": self.W, "max": None}},
                     "max_leverage": 125, "maker": 0.0001234, "taker": 0.0005678,
                     "active": True},
        })
        self.assertLess(len(out), self.BUDGET)

    def test_indicators_full_set(self):
        panel = {
            "rsi": self.W, "adx": self.W, "atr": self.W,
            "ema": {"ema20": self.W, "ema50": self.W},
            "sma": {"sma20": self.W, "sma50": self.W},
            "macd": {"macd": self.W, "signal": self.W, "hist": self.W},
            "bollinger": {k: self.W for k in ("mid", "upper", "lower", "sd", "z")},
        }
        out = shaping.shape_indicators({"symbol": self.SYM, "interval": "1M",
                                        "as_of": 1718064000000, "last_close": self.W,
                                        "indicators": panel,
                                        "stale": True, "stale_age_s": 12345.6})
        self.assertLess(len(out), self.BUDGET)

    def test_funding_current_and_history_page(self):
        cur = shaping.shape_funding({"symbol": self.SYM, "rate": -0.00012345,
                                     "mark": self.W, "index": self.W,
                                     "next_funding_ts": 1718064000000,
                                     "interval_hours": 8})
        self.assertLess(len(cur), self.BUDGET)
        hist = shaping.shape_funding_history(_envelope(
            [{"ts": 1718064000000, "rate": -0.00012345}] * 30,
            total=1000, has_more=True))
        self.assertLess(len(hist), self.BUDGET)

    def test_indices_snapshot(self):
        item = {"key": "btc-dominance", "label": "US Dollar Index (DXY)",
                "available": True, "value": self.W, "unit": "%",
                "classification": "Extreme Greed", "change_pct": -99.999,
                "stale": True}
        out = shaping.shape_indices({"items": [item] * 8})
        self.assertLess(len(out), self.BUDGET)

    def test_positions_page(self):
        # 50 rows = the page size the positions tool requests; memo at the
        # engine's 300-char cap (clip() trims it to MEMO_MAX per row)
        row = _pos(symbol=self.SYM, qty=self.W, entry=self.W, mark=self.W,
                   roi_pct=-1234.56, leverage=125, liq_distance_pct=99.99,
                   protection={"take_profit": True, "stop_loss": True,
                               "sl_qty_covers": False},
                   memo="長" * 300)
        out = shaping.shape_positions(_envelope([row] * 50, total=60, has_more=True))
        self.assertLess(len(out), self.BUDGET)

    def test_balance_line(self):
        out = shaping.shape_balance({"equity": self.W, "wallet": self.W,
                                     "free": self.W, "used": self.W,
                                     "unrealized_pnl": -self.W})
        self.assertLess(len(out), self.BUDGET)

    def test_protection_status(self):
        leg = {"id": "9" * 20, "trigger_price": self.W, "status": "partially_filled"}
        out = shaping.shape_protection_status({
            "symbol": self.SYM,
            "position": {"side": "short", "qty": self.W, "entry": self.W,
                         "mark": self.W, "leverage": 125},
            "take_profit": leg, "stop_loss": leg,
            "tp_legs": 99, "sl_legs": 99, "sl_qty_covers": False})
        self.assertLess(len(out), self.BUDGET)


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


# ── Phase 3 write-result shapers ──────────────────────────────────────────────

def _norm(**over):
    """The engine's _norm_order shape (perp router)."""
    row = {"id": "123", "symbol": "BTCUSDT", "type": "market", "side": "buy",
           "status": "filled", "price": None, "amount": 0.002, "filled": 0.002,
           "reduce_only": False, "trigger_price": None, "ts": 1718064000000,
           "algo": False}
    row.update(over)
    return row


class OrderResultTest(unittest.TestCase):
    def test_market_fill_with_legs(self):
        out = shaping.shape_order_result({
            "ok": True, "applied": {"leverage": 5, "margin_mode": "isolated"},
            "order": _norm(), "memo": "x",
            "take_profit": _norm(id="111", type="take_profit_market", side="sell",
                                 status="new", trigger_price=70000.0, filled=0.0,
                                 algo=True),
            "stop_loss": _norm(id="222", type="stop_market", side="sell",
                               status="new", trigger_price=60000.0, filled=0.0,
                               algo=True),
        })
        self.assertIn("placed: BTCUSDT buy #123 market @market qty 0.002 filled", out)
        self.assertNotIn("filled filled", out)  # full fill isn't repeated
        self.assertIn("applied: leverage 5x · margin_mode isolated", out)
        self.assertIn("TP leg #111 take_profit_market trig 70000 qty 0.002 new · algo", out)
        self.assertIn("SL leg #222 stop_market trig 60000 qty 0.002 new · algo", out)
        self.assertTrue(out.endswith("next: verify with protection_status, then positions"))

    def test_limit_resting_partial_fill(self):
        out = shaping.shape_order_result({
            "ok": True, "applied": {},
            "order": _norm(type="limit", status="new", price=62000.0, filled=0.001)})
        self.assertIn("placed: BTCUSDT buy #123 limit @62000 qty 0.002 new filled 0.001", out)
        self.assertNotIn("applied:", out)   # nothing applied → no line
        self.assertNotIn("TP leg", out)

    def test_margin_mode_note_warns(self):
        out = shaping.shape_order_result({
            "ok": True, "order": _norm(),
            "applied": {"margin_mode": "cross",
                        "margin_mode_note": "unchanged: a position exists (-4047)"}})
        self.assertIn("⚠ unchanged: a position exists (-4047)", out)


class CloseResultTest(unittest.TestCase):
    def test_close_sweeps_legs(self):
        out = shaping.shape_close_result({
            "ok": True,
            "closed": _norm(id="999", side="sell", reduce_only=True),
            "cancelled_protection": ["111", "222"]})
        self.assertIn("closed: BTCUSDT sell #999 market @market qty 0.002 filled", out)
        self.assertIn("cancelled protection legs: #111, #222", out)
        self.assertTrue(out.endswith("next: confirm flat via positions / protection_status"))

    def test_no_legs_to_sweep(self):
        out = shaping.shape_close_result({
            "ok": True, "closed": _norm(id="999", side="sell"),
            "cancelled_protection": []})
        self.assertIn("cancelled protection legs: none", out)

    def test_sweep_error_and_cancel_failed_warn(self):
        out = shaping.shape_close_result({
            "ok": True, "closed": _norm(),
            "protection_sweep_error": "Timeout: x"})
        self.assertIn("⚠ protection sweep failed: Timeout: x", out)
        self.assertIn("orphan legs", out)
        out = shaping.shape_close_result({
            "ok": True, "closed": _norm(),
            "cancelled_protection": ["111"], "cancel_failed": ["222"]})
        self.assertIn("⚠ cancel failed: #222 — cancel manually via cancel_order", out)


class ProtectionResultTest(unittest.TestCase):
    def test_replaced_legs(self):
        out = shaping.shape_protection_result({
            "ok": True, "symbol": "BTCUSDT",
            "stop_loss": _norm(id="333", type="stop_market", side="sell",
                               status="new", trigger_price=61000.0, filled=0.0,
                               algo=True),
            "replaced": ["222"]})
        self.assertIn("BTCUSDT protection updated", out)
        self.assertIn("SL leg #333 stop_market trig 61000 qty 0.002 new · algo", out)
        self.assertIn("replaced old legs: #222", out)
        self.assertNotIn("TP leg", out)
        self.assertTrue(out.endswith("next: verify with protection_status"))

    def test_fresh_attach_and_cancel_failed(self):
        out = shaping.shape_protection_result({
            "ok": True, "symbol": "ETHUSDT",
            "take_profit": _norm(id="444", type="take_profit_market",
                                 trigger_price=4000.0, status="new", filled=0.0),
            "replaced": [], "cancel_failed": ["555"]})
        self.assertIn("replaced old legs: none", out)
        self.assertIn("⚠ old legs still resting: #555", out)
        self.assertIn("cancel via cancel_order", out)


class CancelAndConfigResultTest(unittest.TestCase):
    def test_cancel_one(self):
        out = shaping.shape_cancel_result({"ok": True, "cancelled": "777"})
        self.assertIn("cancelled #777", out)
        self.assertIn("next: re-check open_orders", out)

    def test_cancel_all_warns_naked(self):
        out = shaping.shape_cancel_all_result({"ok": True, "symbol": "BTCUSDT"})
        self.assertIn("BTCUSDT: all resting orders cancelled", out)
        self.assertIn("NAKED", out)
        self.assertIn("set_protection", out)

    def test_leverage_margin_both_ok(self):
        out = shaping.shape_leverage_margin(
            {"ok": True, "symbol": "BTCUSDT", "margin_mode": "isolated", "result": "set"},
            {"ok": True, "symbol": "BTCUSDT", "leverage": 5})
        self.assertEqual(out, "margin_mode: isolated (set)\nleverage: 5x set")

    def test_leverage_margin_half_success_visible(self):
        out = shaping.shape_leverage_margin(
            None, {"ok": True, "leverage": 3},
            margin_error="[sunday 409] cannot change margin mode while a position exists")
        self.assertIn("margin_mode: [sunday 409]", out)
        self.assertIn("leverage: 3x set", out)

    def test_leverage_margin_single_segment(self):
        self.assertEqual(
            shaping.shape_leverage_margin(None, {"leverage": 10}),
            "leverage: 10x set")
        self.assertEqual(
            shaping.shape_leverage_margin(
                {"margin_mode": "cross", "result": "unchanged"}, None),
            "margin_mode: cross (unchanged)")


class AlertShapersTest(unittest.TestCase):
    ROW = {"id": 7, "symbol": "BTCUSDT", "kind": "price_above", "threshold": 70000.0,
           "ref_price": None, "note": "breakout watch", "status": "active",
           "created_at": "2026-06-12T03:00:00Z", "triggered_at": None,
           "triggered_price": None}

    def test_created(self):
        out = shaping.shape_alert_created(self.ROW)
        self.assertIn("#7 BTCUSDT price_above 70000 active | breakout watch", out)
        self.assertIn("fires once", out)

    def test_pct_move_shows_ref(self):
        out = shaping.shape_alert_created(
            {**self.ROW, "kind": "pct_move", "threshold": 5.0, "ref_price": 63666.1})
        self.assertIn("pct_move 5 active (ref 63666.1)", out)

    def test_list_with_triggered_and_tail(self):
        fired = {**self.ROW, "id": 5, "status": "triggered", "note": None,
                 "triggered_price": 70123.4, "triggered_at": "2026-06-12T04:00:00Z"}
        out = shaping.shape_alerts_list(_envelope([self.ROW, fired]))
        self.assertIn("#7 BTCUSDT price_above 70000 active", out)
        self.assertIn("#5 BTCUSDT price_above 70000 triggered → fired @70123.4", out)
        self.assertIn("has_more: false", out)

    def test_empty_and_delete(self):
        self.assertIn("no alerts", shaping.shape_alerts_list(_envelope([])))
        self.assertEqual(shaping.shape_alert_deleted({"ok": True, "deleted": 5}),
                         "deleted alert #5")


class Phase3BudgetTest(unittest.TestCase):
    """Worst-legal-input budget locks for the write tools (S3) — small by
    construction, locked anyway so a future verbose rendering fails loudly."""

    BUDGET = 60_000
    W = 123456.7890123456
    BIG_ID = "9" * 20

    def _leg(self):
        return _norm(id=self.BIG_ID, type="take_profit_market", status="new",
                     trigger_price=self.W, amount=self.W, filled=0.0, algo=True)

    def test_order_close_protection_results(self):
        order = shaping.shape_order_result({
            "ok": True, "order": _norm(id=self.BIG_ID, amount=self.W),
            "applied": {"leverage": 125, "margin_mode": "isolated",
                        "margin_mode_note": "x" * 120},
            "take_profit": self._leg(), "stop_loss": self._leg()})
        close = shaping.shape_close_result({
            "ok": True, "closed": _norm(id=self.BIG_ID, amount=self.W),
            "cancelled_protection": [self.BIG_ID] * 99,
            "cancel_failed": [self.BIG_ID] * 99})
        prot = shaping.shape_protection_result({
            "ok": True, "symbol": "1000000BABYDOGEUSDT",
            "take_profit": self._leg(), "stop_loss": self._leg(),
            "replaced": [self.BIG_ID] * 99, "cancel_failed": [self.BIG_ID] * 99})
        for out in (order, close, prot):
            self.assertLess(len(out), self.BUDGET)

    def test_cancel_config_alert_results(self):
        outs = [
            shaping.shape_cancel_result({"cancelled": self.BIG_ID}),
            shaping.shape_cancel_all_result({"symbol": "1000000BABYDOGEUSDT"}),
            shaping.shape_leverage_margin(None, None, "e" * 500, "e" * 500),
            shaping.shape_alert_created({"id": 10 ** 9, "symbol": "1000000BABYDOGEUSDT",
                                         "kind": "pct_move", "threshold": self.W,
                                         "ref_price": self.W, "note": "n" * 120,
                                         "status": "active"}),
        ]
        for out in outs:
            self.assertLess(len(out), self.BUDGET)

    def test_alerts_list_page(self):
        row = {"id": 10 ** 9, "symbol": "1000000BABYDOGEUSDT", "kind": "pct_move",
               "threshold": self.W, "ref_price": self.W, "note": "n" * 120,
               "status": "triggered", "triggered_price": self.W,
               "triggered_at": "2026-06-12T04:00:00Z"}
        out = shaping.shape_alerts_list(_envelope([row] * 30, total=300, has_more=True))
        self.assertLess(len(out), self.BUDGET)


if __name__ == "__main__":
    unittest.main()
