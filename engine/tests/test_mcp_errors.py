"""sunday_mcp.errors — known-code hint lines (PRD-9.2 §3) + passthrough."""

from __future__ import annotations

import unittest

from sunday_mcp import errors
from sunday_mcp.client import Reply


def _reply(status: int, text: str) -> Reply:
    return Reply(status=status, json=None, text=text)


class HintTest(unittest.TestCase):
    def test_percent_price(self):
        out = errors.upstream_error_text(_reply(400, 'binance -4016 PERCENT_PRICE'))
        self.assertIn("[sunday 400] binance -4016 PERCENT_PRICE", out)
        self.assertIn("re-quote near current price", out)

    def test_clock_skew(self):
        out = errors.upstream_error_text(_reply(400, "code -1021 Timestamp ahead"))
        self.assertIn("clock skew", out)
        self.assertIn("POST /api/reports", out)

    def test_unknown_order(self):
        out = errors.upstream_error_text(_reply(400, "-2011 Unknown order sent"))
        self.assertIn("refresh open_orders first", out)

    def test_wrong_side_trigger_400(self):
        out = errors.upstream_error_text(
            _reply(400, "stop_loss 70000 would trigger immediately (long)"))
        self.assertIn("trigger price on the wrong side", out)

    def test_trigger_hint_only_on_400(self):
        out = errors.upstream_error_text(_reply(500, "trigger service exploded"))
        self.assertNotIn("wrong side", out)

    def test_503_degrade_hint(self):
        out = errors.upstream_error_text(_reply(503, "upstream busy"))
        self.assertIn("fall back to http_request", out)

    def test_known_code_wins_over_trigger_keyword(self):
        out = errors.upstream_error_text(
            _reply(400, "-4016 PERCENT_PRICE: trigger too far"))
        self.assertIn("re-quote", out)
        self.assertNotIn("wrong side", out)

    def test_unknown_error_is_bare_passthrough(self):
        out = errors.upstream_error_text(_reply(422, '{"detail": "title too long"}'))
        self.assertEqual(out, '[sunday 422] {"detail": "title too long"}')

    def test_body_whitespace_trimmed(self):
        out = errors.upstream_error_text(_reply(404, "not found\n"))
        self.assertEqual(out, "[sunday 404] not found")


if __name__ == "__main__":
    unittest.main()
