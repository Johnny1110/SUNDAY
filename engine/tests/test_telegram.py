"""Unit tests for the Telegram notifier (pure text builders + the no-op/escape contract).

No network: we test the formatters directly and assert `send` is a silent no-op when the
bot token / chat id aren't configured (the default), so an unconfigured deploy is unchanged.
"""

import sys
import types
import unittest

from sunday import telegram as T


class TestTelegramFormatters(unittest.TestCase):
    def test_report_text_has_title_kind_icon(self):
        msg = T.report_text("Big win", "closed BTC +$420", kind="profit")
        self.assertIn("Big win", msg)
        self.assertIn("profit", msg)
        self.assertIn("🟢", msg)            # profit icon
        self.assertIn("closed BTC", msg)

    def test_report_text_escapes_html(self):
        msg = T.report_text("a <b>& b", "x < y & z > w", kind="info")
        self.assertIn("&lt;", msg)
        self.assertIn("&amp;", msg)
        self.assertNotIn("<b>&", msg)        # the raw title's angle brackets are escaped

    def test_report_text_clips_long_body(self):
        msg = T.report_text("t", "x" * 5000, kind="info")
        self.assertIn("…", msg)
        self.assertLess(len(msg), 1200)      # body trimmed to ~700 chars

    def test_alert_text_above(self):
        msg = T.alert_text(
            {"symbol": "ETHUSDT", "kind": "price_above", "threshold": 4000, "note": "watch"}, 4010)
        self.assertIn("ETHUSDT", msg)
        self.assertIn("4000", msg)
        self.assertIn("4010", msg)
        self.assertIn("watch", msg)          # note rendered

    def test_alert_text_pct_move_no_note(self):
        msg = T.alert_text({"symbol": "BTCUSDT", "kind": "pct_move", "threshold": 3, "note": None}, 64000)
        self.assertIn("BTCUSDT", msg)
        self.assertIn("±", msg)
        self.assertNotIn("📝", msg)          # no note → no note line

    def test_position_text_from_event_payload(self):
        ev = {"title": "BTCUSDT long ROI ▲+15.0%",
              "data": {"symbol": "BTCUSDT", "side": "long", "roi_pct": 15.0,
                       "unrealized_pnl": 30.0, "mark": 105.0, "entry": 100.0}}
        msg = T.position_text(ev)
        self.assertIn("BTCUSDT", msg)
        self.assertIn("long", msg)
        self.assertIn("▲", msg)
        self.assertIn("15.0%", msg)

    def test_position_text_handles_missing_roi(self):
        ev = {"title": "BTCUSDT long step", "data": {"symbol": "BTCUSDT", "side": "long"}}
        msg = T.position_text(ev)                # roi None → falls back to the title line
        self.assertIn("BTCUSDT", msg)
        self.assertIn("持倉", msg)

    def test_send_is_noop_when_unconfigured(self):
        # Blank token/chat → send must not raise and must report no-op. Stub sunday.config
        # so the test doesn't depend on the developer's real engine/.env having TELEGRAM_*.
        stub = types.ModuleType("sunday.config")
        stub.settings = types.SimpleNamespace(telegram_bot_token="", telegram_chat_id="")
        prev = sys.modules.get("sunday.config")
        sys.modules["sunday.config"] = stub
        try:
            self.assertFalse(T.enabled())
            status, ok = T.send("hello")
            self.assertIsNone(status)
            self.assertFalse(ok)
        finally:
            if prev is not None:
                sys.modules["sunday.config"] = prev
            else:
                sys.modules.pop("sunday.config", None)


if __name__ == "__main__":
    unittest.main()
