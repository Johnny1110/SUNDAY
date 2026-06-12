"""sunday_mcp.client — retry policy (S5), error passthrough, X-Agent (S4).

All fakes, no network: the module-level `opener` / `_sleep` are the test seams.
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error

from sunday_mcp import client


class FakeResp:
    def __init__(self, status=200, body=b'{"ok": true}'):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Pops one outcome per call: an Exception is raised, anything else returned."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _conn_err():
    return urllib.error.URLError(ConnectionRefusedError(111, "refused"))


def _http_err(code=400, body=b'{"detail": "bad request"}'):
    return urllib.error.HTTPError("http://x/api", code, "Bad Request", None, io.BytesIO(body))


class ClientTest(unittest.TestCase):
    def setUp(self):
        self._opener, self._gap = client.opener, client._sleep
        self.naps = []
        client._sleep = self.naps.append

    def tearDown(self):
        client.opener, client._sleep = self._opener, self._gap

    def _install(self, *outcomes) -> FakeOpener:
        fake = FakeOpener(*outcomes)
        client.opener = fake
        return fake

    # ── retry policy (S5) ─────────────────────────────────────────────────────

    def test_get_retries_once_then_succeeds(self):
        fake = self._install(_conn_err(), FakeResp())
        r = client.call("GET", "/api/markets")
        self.assertEqual(r.status, 200)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(self.naps, [client.RETRY_GAP_S])

    def test_get_two_connection_failures_raise(self):
        fake = self._install(_conn_err(), _conn_err())
        with self.assertRaises(client.EngineUnreachable):
            client.call("GET", "/api/markets")
        self.assertEqual(len(fake.calls), 2)

    def test_post_never_retries(self):
        fake = self._install(_conn_err())
        with self.assertRaises(client.EngineUnreachable):
            client.call("POST", "/api/perp/order", body={"symbol": "BTCUSDT"})
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.naps, [])

    def test_delete_never_retries(self):
        # cancel paths (DELETE) sit under the same S5 rule as POST
        fake = self._install(_conn_err())
        with self.assertRaises(client.EngineUnreachable):
            client.call("DELETE", "/api/perp/orders", query={"symbol": "X"},
                        agent="friday")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.naps, [])

    def test_get_retry_optout(self):
        # the health probe path: fast failure, no 1s gap
        fake = self._install(_conn_err())
        with self.assertRaises(client.EngineUnreachable):
            client.call("GET", "/health", retry=False)
        self.assertEqual(len(fake.calls), 1)

    def test_http_4xx_is_a_reply_not_a_retry(self):
        fake = self._install(_http_err(400))
        r = client.call("GET", "/api/markets")
        self.assertEqual(r.status, 400)
        self.assertEqual(r.json, {"detail": "bad request"})
        self.assertEqual(len(fake.calls), 1)  # a real answer — even on GET

    # ── request construction ──────────────────────────────────────────────────

    def test_x_agent_header_only_when_given(self):
        fake = self._install(FakeResp(), FakeResp())
        client.call("POST", "/api/perp/close", body={"symbol": "X"}, agent="friday")
        client.call("GET", "/api/account/positions")
        with_agent, without = fake.calls[0][0], fake.calls[1][0]
        self.assertEqual(with_agent.get_header("X-agent"), "friday")
        self.assertIsNone(without.get_header("X-agent"))

    def test_query_encoding_skips_none(self):
        fake = self._install(FakeResp())
        client.call("GET", "/api/markets", query={"page": 1, "symbol": None})
        url = fake.calls[0][0].full_url
        self.assertTrue(url.startswith(client.BASE_URL + "/api/markets?"))
        self.assertIn("page=1", url)
        self.assertNotIn("symbol", url)

    def test_json_body_and_content_type(self):
        fake = self._install(FakeResp())
        client.call("POST", "/api/perp/order", body={"qty": 0.5})
        req = fake.calls[0][0]
        self.assertEqual(json.loads(req.data), {"qty": 0.5})
        self.assertEqual(req.get_header("Content-type"), "application/json")

    def test_non_json_body_yields_text_only(self):
        self._install(FakeResp(body=b"plain text manual"))
        r = client.call("GET", "/manual")
        self.assertIsNone(r.json)
        self.assertEqual(r.text, "plain text manual")

    # ── probe_health ──────────────────────────────────────────────────────────

    def test_probe_health_up_and_down(self):
        self._install(FakeResp(body=b'{"ok": true, "service": "sunday"}'))
        self.assertEqual(client.probe_health(), {"reachable": True, "status": 200})
        fake = self._install(_conn_err())
        self.assertEqual(client.probe_health(), {"reachable": False, "status": None})
        self.assertEqual(len(fake.calls), 1)  # retry=False inside the probe


if __name__ == "__main__":
    unittest.main()
