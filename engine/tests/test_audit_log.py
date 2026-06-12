"""BUG-03 regression tests — every order-book write must be attributable.

The HYPE mystery close (2026-06-11) was uninvestigable because Sunday kept no record
of WHO mutated the book. The contract pinned here:

  * /api/perp writes take an ``X-Agent`` header and record (agent, action) per
    mutation in order_log — entries, TP/SL legs, closes and cancels alike
  * the position-memo join only ever sees action='order' rows (audit rows must not
    displace the agent's rationale)
  * /api/account orders + trades rows carry ``agent`` (null = unattributable) and
    accept an ``agent=`` filter; cancel rows never re-attribute someone else's order
  * pre-audit-log databases gain the new columns on connect (additive migration)

Needs the engine deps installed (ccxt/fastapi), same as test_tpsl_visibility.py.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from sunday import store
from sunday.routers import account, perp

from .test_tpsl_safety import ENTRY, _ccxt_leg

OPEN_ROW = {"orderId": 900, "symbol": "BNBUSDT", "type": "STOP_MARKET", "side": "SELL",
            "origQty": "0.01", "executedQty": "0", "reduceOnly": True, "closePosition": False,
            "stopPrice": "650.0", "time": 1750514941540, "algo": True, "clientOrderId": "x1"}


class TestAuditStore(unittest.TestCase):
    def setUp(self):
        store.connect(":memory:")

    def tearDown(self):
        store.close()

    def test_latest_order_ignores_audit_rows(self):
        store.record_order("X", "1", "entry memo", {"side": "buy"}, agent="friday")
        store.record_order("X", "2", None, {}, agent="trader", action="protection")
        store.record_order("X", "3", None, {}, agent="trader", action="close")
        got = store.latest_order("X")
        self.assertEqual((got["order_id"], got["memo"], got["agent"]), ("1", "entry memo", "friday"))

    def test_agents_by_order_id_scopes_and_excludes(self):
        store.record_order("A", "1", None, {}, agent="friday")
        store.record_order("B", "2", None, {}, agent="trader", action="close")
        store.record_order("B", "3", None, {})              # anonymous → not attributable
        self.assertEqual(store.agents_by_order_id(), {"1": "friday", "2": "trader"})
        self.assertEqual(store.agents_by_order_id("B"), {"2": "trader"})

    def test_cancel_rows_never_reattribute(self):
        # friday placed 900; trader cancelled it later — the order stays friday's.
        store.record_order("B", "900", None, {}, agent="friday", action="order")
        store.record_order("B", "900", None, {}, agent="trader", action="cancel")
        self.assertEqual(store.agents_by_order_id()["900"], "friday")


class TestSchemaMigration(unittest.TestCase):
    def test_old_databases_gain_the_audit_columns(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.db")
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE order_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, symbol TEXT NOT NULL,
                order_id TEXT, side TEXT, type TEXT, qty REAL, notional_usd REAL, price REAL,
                leverage INTEGER, margin_mode TEXT, reduce_only INTEGER, take_profit REAL,
                stop_loss REAL, memo VARCHAR(300))""")
            conn.execute("INSERT INTO order_log (ts, symbol, order_id, memo) "
                         "VALUES ('t', 'BTCUSDT', '1', 'pre-audit row')")
            conn.commit()
            conn.close()

            store.connect(path)
            try:
                old = store.latest_order("BTCUSDT")     # backfilled action='order'
                self.assertEqual(old["memo"], "pre-audit row")
                self.assertIsNone(old["agent"])         # history stays unattributed (null)
                store.record_order("BTCUSDT", "2", None, {}, agent="trader", action="close")
                self.assertEqual(store.agents_by_order_id(), {"2": "trader"})
            finally:
                store.close()


class TestPerpAuditRecords(unittest.TestCase):
    def setUp(self):
        store.connect(":memory:")

    def tearDown(self):
        store.close()

    def test_entry_and_its_legs_are_attributed(self):
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "fetch_mark_price", return_value=720.0), \
             mock.patch.object(perp.exchange, "amount_to_precision", side_effect=lambda s, a: a), \
             mock.patch.object(perp.exchange, "create_order", return_value=ENTRY), \
             mock.patch.object(perp.exchange, "place_stop",
                               return_value=_ccxt_leg(900, 650.0, False)):
            perp.place_order(perp.OrderReq(symbol="BNBUSDT", side="buy", qty=0.01,
                                           stop_loss=650.0, memo="trend long"),
                             x_agent="trader")
        agents = store.agents_by_order_id("BNBUSDT")
        self.assertEqual(agents.get("777"), "trader")   # the entry
        self.assertEqual(agents.get("900"), "trader")   # the SL leg (algoId)
        latest = store.latest_order("BNBUSDT")
        self.assertEqual(latest["memo"], "trend long")  # leg rows don't displace the memo
        self.assertEqual(latest["order_id"], "777")

    def test_close_is_attributed_and_the_entry_memo_survives(self):
        store.record_order("BNBUSDT", "777", "the entry", {"side": "buy"}, agent="friday")
        closed = dict(ENTRY, id="888", side="sell")
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "close_position", return_value=closed), \
             mock.patch.object(perp.exchange, "sweep_orphan_legs", return_value=([], [])):
            perp.close(perp.CloseReq(symbol="BNBUSDT"), x_agent="trader")
        self.assertEqual(store.agents_by_order_id().get("888"), "trader")
        self.assertEqual(store.latest_order("BNBUSDT")["memo"], "the entry")

    def test_anonymous_writes_still_work_but_stay_null(self):
        # No X-Agent header (direct call leaves the FastAPI Header default in place).
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "fetch_mark_price", return_value=None), \
             mock.patch.object(perp.exchange, "amount_to_precision", side_effect=lambda s, a: a), \
             mock.patch.object(perp.exchange, "create_order", return_value=ENTRY):
            out = perp.place_order(perp.OrderReq(symbol="BNBUSDT", side="buy", qty=0.01))
        self.assertTrue(out["ok"])
        self.assertIsNone(store.latest_order("BNBUSDT")["agent"])
        self.assertEqual(store.agents_by_order_id(), {})

    def test_audit_trouble_never_fails_a_landed_order(self):
        store.close()  # simulate the store being unavailable mid-flight
        try:
            with mock.patch.object(perp, "require_trade_key", lambda: None), \
                 mock.patch.object(perp.exchange, "fetch_mark_price", return_value=None), \
                 mock.patch.object(perp.exchange, "amount_to_precision", side_effect=lambda s, a: a), \
                 mock.patch.object(perp.exchange, "create_order", return_value=ENTRY):
                out = perp.place_order(perp.OrderReq(symbol="BNBUSDT", side="buy", qty=0.01),
                                       x_agent="trader")
            self.assertTrue(out["ok"])      # the order DID land — a 5xx would trigger a retry
        finally:
            store.connect(":memory:")       # so tearDown's close() finds a connection


class TestAccountAgentJoin(unittest.TestCase):
    def setUp(self):
        store.connect(":memory:")

    def tearDown(self):
        store.close()

    def test_open_orders_carry_and_filter_by_agent(self):
        store.record_order("BNBUSDT", "900", None, {}, agent="trader", action="protection")
        rows = [OPEN_ROW, dict(OPEN_ROW, orderId=901)]
        with mock.patch.object(account, "require_trade_key", lambda: None), \
             mock.patch.object(account.exchange, "fetch_open_orders", return_value=rows), \
             mock.patch.object(account.exchange, "leverage_by_symbol", return_value={}):
            out = account.open_orders(symbol="BNBUSDT")
            only = account.open_orders(symbol="BNBUSDT", agent="trader")
        self.assertEqual({r["id"]: r["agent"] for r in out["items"]},
                         {"900": "trader", "901": None})
        self.assertEqual([r["id"] for r in only["items"]], ["900"])

    def test_trades_attribute_via_their_placing_order(self):
        store.record_order("BNBUSDT", "888", None, {}, agent="friday", action="close")
        trade = {"id": 1, "orderId": 888, "symbol": "BNBUSDT", "side": "SELL", "price": "700",
                 "qty": "0.01", "quoteQty": "7.0", "commission": "0.01",
                 "commissionAsset": "USDT", "realizedPnl": "1.0", "time": 1}
        with mock.patch.object(account, "require_trade_key", lambda: None), \
             mock.patch.object(account.exchange, "fetch_my_trades", return_value=[trade]):
            out = account.trade_history(symbol="BNBUSDT")
        self.assertEqual(out["items"][0]["agent"], "friday")

    def test_store_trouble_degrades_to_unknown_not_an_error(self):
        store.close()  # listings must survive without the audit db
        try:
            with mock.patch.object(account, "require_trade_key", lambda: None), \
                 mock.patch.object(account.exchange, "fetch_open_orders", return_value=[OPEN_ROW]), \
                 mock.patch.object(account.exchange, "leverage_by_symbol", return_value={}):
                out = account.open_orders(symbol="BNBUSDT")
            self.assertIsNone(out["items"][0]["agent"])
        finally:
            store.connect(":memory:")


if __name__ == "__main__":
    unittest.main()
