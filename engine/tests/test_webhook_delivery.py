"""End-to-end webhook-delivery tests (req 5/6): the REAL Sunday → evva-swarm path.

The other suites inject a fake `notify` and check pure rules. These instead stand up a
loopback HTTP receiver mimicking the swarm's `POST /api/swarm/sunday/event` and assert
the *production* path actually delivers — engine fire → the real default notifier →
`events.post` (stdlib urllib) → HTTP — carrying the `{title, body, data, to}` payload
with `to:"leader"` (which the swarm resolves to the leader, friday). Stdlib only
(http.server, urllib, threading); no ccxt / fastapi / pydantic needed.
"""

from __future__ import annotations

import json
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from sunday import events, store


class _Receiver:
    """A loopback stand-in for the evva-swarm webhook endpoint (req 5/6)."""

    def __init__(self, status: int = 202):
        self.received: list[dict] = []
        recv, st = self.received, status

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = None
                recv.append({"path": self.path,
                             "ctype": self.headers.get("Content-Type"),
                             "json": body})
                self.send_response(st)
                self.end_headers()

            def do_GET(self):  # evva-style health endpoint, for events.probe
                self.send_response(st if self.path == "/healthz" else 404)
                self.end_headers()

            def log_message(self, *a):  # keep test output quiet
                pass

        self._srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/api/swarm/sunday/event"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()


class _ConfigStub:
    """Install a fake `sunday.config` (pydantic_settings isn't a test dep) so the REAL
    default notifiers (`_default_alert_notify` / `_default_position_notify`) run against
    our receiver instead of needing a loaded Settings object."""

    def __init__(self, url: str, step_pct: float = 5.0):
        self._mod = types.ModuleType("sunday.config")
        self._mod.settings = types.SimpleNamespace(
            evva_webhook_url=url, monitor_step_pct=step_pct, monitor_enabled=True)
        self._prev = sys.modules.get("sunday.config")

    def __enter__(self):
        sys.modules["sunday.config"] = self._mod
        return self

    def __exit__(self, *a):
        if self._prev is not None:
            sys.modules["sunday.config"] = self._prev
        else:
            sys.modules.pop("sunday.config", None)


class TestPostContract(unittest.TestCase):
    """events.post + builders → the exact wire shape the swarm consumes, over real HTTP."""

    def setUp(self):
        self.rx = _Receiver()
        self.addCleanup(self.rx.stop)

    def test_price_alert_delivered(self):
        alert = {"id": 7, "symbol": "ETHUSDT", "kind": "price_above", "threshold": 4000, "note": "突破"}
        status, ok = events.post(self.rx.url, events.price_alert_event(alert, 4010.5))
        self.assertEqual((status, ok), (202, True))
        self.assertEqual(len(self.rx.received), 1)
        got = self.rx.received[0]
        self.assertEqual(got["path"], "/api/swarm/sunday/event")
        self.assertEqual(got["ctype"], "application/json")
        p = got["json"]
        self.assertEqual(p["to"], "leader")                 # ← routed to friday (the leader)
        self.assertEqual(p["data"]["event_type"], "price_alert")
        self.assertEqual(p["data"]["symbol"], "ETHUSDT")
        self.assertEqual(p["data"]["price"], 4010.5)
        self.assertIn("suggested_action", p["data"])
        self.assertTrue(p["title"] and p["body"])

    def test_position_pnl_delivered(self):
        ev = events.position_pnl_event("BTCUSDT", "long", 10.0, 30.0, 110.0, 100.0, 5.0)
        status, ok = events.post(self.rx.url, ev)
        self.assertEqual((status, ok), (202, True))
        p = self.rx.received[0]["json"]
        self.assertEqual(p["to"], "leader")
        self.assertEqual(p["data"]["event_type"], "position_pnl")
        self.assertEqual(p["data"]["symbol"], "BTCUSDT")
        self.assertEqual(p["data"]["roi_pct"], 10.0)
        self.assertIn("suggested_action", p["data"])

    def test_explicit_recipient_carried_verbatim(self):
        # If a caller ever targets a member by name, the payload carries it through.
        events.post(self.rx.url, events.position_pnl_event(
            "BTCUSDT", "short", -5.0, -10.0, 105.0, 100.0, 5.0, to="friday"))
        self.assertEqual(self.rx.received[0]["json"]["to"], "friday")


class TestPostResilience(unittest.TestCase):
    def test_never_raises_when_swarm_down(self):
        # Closed port → urlopen fails; post must swallow it so Sunday keeps serving,
        # and log a warning so the drop is observable (a lost event = an agent never woken).
        with self.assertLogs("sunday.events", level="WARNING") as cm:
            status, ok = events.post("http://127.0.0.1:1/api/swarm/sunday/event",
                                     {"title": "t", "body": "x"}, timeout=0.5)
        self.assertEqual((status, ok), (None, False))
        self.assertIn("failed", cm.output[0])

    def test_empty_url_dropped_with_warning(self):
        # Misconfigured EVVA_WEBHOOK_URL must not look like a network blip.
        with self.assertLogs("sunday.events", level="WARNING") as cm:
            status, ok = events.post("", {"title": "t", "body": "x"})
        self.assertEqual((status, ok), (None, False))
        self.assertIn("EVVA_WEBHOOK_URL empty", cm.output[0])

    def test_non_2xx_logged(self):
        rx = _Receiver(status=500)
        self.addCleanup(rx.stop)
        with self.assertLogs("sunday.events", level="WARNING") as cm:
            status, ok = events.post(rx.url, {"title": "t", "body": "x"})
        self.assertEqual((status, ok), (500, False))
        self.assertIn("rejected", cm.output[0])


class TestProbe(unittest.TestCase):
    """Boot-time reachability check used by the app lifespan."""

    def test_probe_ok_via_healthz(self):
        rx = _Receiver(status=200)
        self.addCleanup(rx.stop)
        self.assertTrue(events.probe(rx.url))

    def test_probe_down(self):
        self.assertFalse(events.probe("http://127.0.0.1:1/api/swarm/sunday/event", timeout=0.5))

    def test_probe_empty_url(self):
        self.assertFalse(events.probe(""))


class TestEngineProductionPath(unittest.TestCase):
    """Full wiring: engine fire → the REAL default notifier → events.post → HTTP."""

    def setUp(self):
        self.rx = _Receiver()
        self.addCleanup(self.rx.stop)
        store.connect(":memory:")
        self.addCleanup(store.close)

    def test_alert_engine_default_notifier_delivers(self):
        from sunday import alerts
        store.create_alert("BTCUSDT", "price_above", 70000, None, "breakout")
        with _ConfigStub(self.rx.url):
            eng = alerts.AlertEngine()           # production default notifier (no inject)
            eng.refresh()
            eng.on_price("BTCUSDT", 70500)       # crosses threshold → fire → real webhook
        self.assertEqual(len(self.rx.received), 1, "alert fire did not deliver a webhook")
        p = self.rx.received[0]["json"]
        self.assertEqual(p["data"]["event_type"], "price_alert")
        self.assertEqual(p["data"]["symbol"], "BTCUSDT")
        self.assertEqual(p["to"], "leader")
        self.assertEqual(store.get_alert(1)["status"], "triggered")

    def test_monitor_default_notifier_delivers(self):
        from sunday import monitor
        with _ConfigStub(self.rx.url):
            mon = monitor.Monitor()              # production default notifier + config step
            mon.book["BTCUSDT"] = {"side": "long", "entry": 100.0, "qty": 1.0, "margin": 100.0, "mark": 100.0}
            mon.buckets["BTCUSDT"] = 0
            mon.on_mark("BTCUSDT", 105.0)        # +5% ROI → crosses a bucket → real webhook
        self.assertEqual(len(self.rx.received), 1, "bucket crossing did not deliver a webhook")
        p = self.rx.received[0]["json"]
        self.assertEqual(p["data"]["event_type"], "position_pnl")
        self.assertEqual(p["data"]["roi_pct"], 5.0)
        self.assertEqual(p["to"], "leader")


if __name__ == "__main__":
    unittest.main()
