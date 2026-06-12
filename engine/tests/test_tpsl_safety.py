"""BUG-01/BUG-02/BUG-04 regression tests — TP/SL legs must never surprise-close.

Two production incidents (docs/prd/bug-report) pinned the contract here:

  * BUG-01/BUG-04: a stop leg placed "safely" vs the mainnet mark executed the moment
    it landed. Root cause: the leg defaulted to workingType=CONTRACT_PRICE — judged on
    the TESTNET last-traded price, which drifts far from the mainnet prices agents
    decide on — and Binance's Algo Service runs an in-zone leg instead of rejecting it
    with -2021. Fix: legs are placed with workingType=MARK_PRICE, and both order +
    protection endpoints refuse a trigger already in its fire zone BEFORE any write.
  * BUG-02: a flattened position left its TP/SL legs resting as orphans.
    Fix: POST /api/perp/close sweeps the symbol's trigger legs after the flatten, and
    the monitor sweeps when it sees a position disappear (test_monitor_refresh.py).

Needs the engine deps installed (ccxt/fastapi), same as test_tpsl_visibility.py.
"""

import unittest
from unittest import mock

from fastapi import HTTPException

from sunday import exchange
from sunday.routers import perp

POSITION_ROW = {
    "symbol": "BNBUSDT", "positionAmt": "0.01", "entryPrice": "700.0",
    "markPrice": "720.0", "leverage": "5", "unRealizedProfit": "0.2",
    "liquidationPrice": "600.0", "marginType": "isolated",
}

ENTRY = {"id": "777", "symbol": "BNB/USDT:USDT", "type": "market", "side": "buy",
         "status": "closed", "price": None, "amount": 0.01, "filled": 0.01,
         "reduceOnly": False, "triggerPrice": None, "timestamp": 1750514941540, "info": {}}


def _ccxt_leg(id_, trigger, tp):
    return {"id": str(id_), "symbol": "BNB/USDT:USDT", "status": "open",
            "type": "TAKE_PROFIT_MARKET" if tp else "STOP_MARKET", "side": "sell",
            "price": None, "amount": 0.01, "filled": 0.0, "reduceOnly": True,
            "triggerPrice": trigger, "timestamp": 1750514941540,
            "info": {"algoId": id_}}


class TestPlaceStopWorkingType(unittest.TestCase):
    def test_trigger_legs_judge_on_mark_price(self):
        # CONTRACT_PRICE (the default) judges on testnet LAST — the BUG-01/04 root cause.
        ex = mock.Mock()
        with mock.patch.object(exchange, "trade_ex", return_value=ex), \
             mock.patch.object(exchange, "unify_trade", side_effect=lambda s: s):
            exchange.place_stop("BNBUSDT", "sell", 0.01, 650.0)
            exchange.place_stop("BNBUSDT", "sell", 0.01, 750.0, take_profit=True)
        ex.create_order.assert_any_call(
            "BNBUSDT", "STOP_MARKET", "sell", 0.01,
            params={"stopPrice": 650.0, "reduceOnly": True, "workingType": "MARK_PRICE"})
        ex.create_order.assert_any_call(
            "BNBUSDT", "TAKE_PROFIT_MARKET", "sell", 0.01,
            params={"stopPrice": 750.0, "reduceOnly": True, "workingType": "MARK_PRICE"})


class TestPlaceOrderTriggerGuard(unittest.TestCase):
    def test_in_zone_stop_loss_rejected_before_any_write(self):
        # Long SL must sit BELOW the mark; 730 ≥ mark 720 would fire on arrival.
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "fetch_mark_price", return_value=720.0), \
             mock.patch.object(perp.exchange, "create_order") as create, \
             mock.patch.object(perp.exchange, "set_leverage") as lev:
            with self.assertRaises(HTTPException) as ctx:
                perp.place_order(perp.OrderReq(symbol="BNBUSDT", side="buy", qty=0.01,
                                               leverage=5, stop_loss=730.0))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("immediately", ctx.exception.detail)
        create.assert_not_called()   # zero side effects: no entry, no leverage change
        lev.assert_not_called()

    def test_in_zone_take_profit_rejected_for_short(self):
        # Short TP must sit BELOW the mark; 750 ≥ mark 720 would fire on arrival.
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "fetch_mark_price", return_value=720.0), \
             mock.patch.object(perp.exchange, "create_order") as create:
            with self.assertRaises(HTTPException) as ctx:
                perp.place_order(perp.OrderReq(symbol="BNBUSDT", side="sell", qty=0.01,
                                               take_profit=750.0))
        self.assertEqual(ctx.exception.status_code, 400)
        create.assert_not_called()

    def test_safe_triggers_pass_the_guard(self):
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "fetch_mark_price", return_value=720.0), \
             mock.patch.object(perp.exchange, "amount_to_precision", side_effect=lambda s, a: a), \
             mock.patch.object(perp.exchange, "create_order", return_value=ENTRY), \
             mock.patch.object(perp.exchange, "place_stop",
                               return_value=_ccxt_leg(900, 650.0, False)) as ps, \
             mock.patch.object(perp.store, "record_order"):
            out = perp.place_order(perp.OrderReq(symbol="BNBUSDT", side="buy", qty=0.01,
                                                 stop_loss=650.0))
        self.assertTrue(out["ok"])
        ps.assert_called_once()

    def test_mark_unavailable_fails_open(self):
        # A testnet feed hiccup must not block trading — workingType=MARK_PRICE is
        # still in force on the leg itself.
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "fetch_mark_price", return_value=None), \
             mock.patch.object(perp.exchange, "amount_to_precision", side_effect=lambda s, a: a), \
             mock.patch.object(perp.exchange, "create_order", return_value=ENTRY), \
             mock.patch.object(perp.exchange, "place_stop",
                               return_value=_ccxt_leg(901, 730.0, False)) as ps, \
             mock.patch.object(perp.store, "record_order"):
            out = perp.place_order(perp.OrderReq(symbol="BNBUSDT", side="buy", qty=0.01,
                                                 stop_loss=730.0))
        self.assertTrue(out["ok"])
        ps.assert_called_once()


def _open_leg(order_id, order_type="STOP_MARKET", ts=100, algo=True):
    return {"orderId": order_id, "symbol": "BNBUSDT", "type": order_type,
            "origQty": "0.01", "reduceOnly": True, "time": ts, "algo": algo}


class TestSweepOrphanLegs(unittest.TestCase):
    def test_cancels_trigger_legs_only(self):
        rows = [_open_leg(1, "STOP_MARKET"), _open_leg(2, "TAKE_PROFIT_MARKET"),
                _open_leg(3, "LIMIT", algo=False)]   # a resting entry is not ours to touch
        with mock.patch.object(exchange, "fetch_open_orders", return_value=rows), \
             mock.patch.object(exchange, "cancel_order") as cancel:
            cancelled, failed = exchange.sweep_orphan_legs("BNBUSDT")
        self.assertEqual(cancelled, ["1", "2"])
        self.assertEqual(failed, [])
        cancel.assert_any_call("1", "BNBUSDT", algo=True)
        self.assertEqual(cancel.call_count, 2)

    def test_before_ms_spares_a_reopened_positions_fresh_legs(self):
        rows = [_open_leg(1, ts=100), _open_leg(2, ts=200)]
        with mock.patch.object(exchange, "fetch_open_orders", return_value=rows), \
             mock.patch.object(exchange, "cancel_order") as cancel:
            cancelled, _ = exchange.sweep_orphan_legs("BNBUSDT", before_ms=150)
        self.assertEqual(cancelled, ["1"])           # ts 200 ≥ 150: the reopen's leg survives
        cancel.assert_called_once_with("1", "BNBUSDT", algo=True)

    def test_already_gone_counts_as_cancelled(self):
        # Binance sometimes auto-drops one leg itself (the BUG-02 inconsistency) —
        # an unknown-order error means the goal (nothing resting) is already met.
        with mock.patch.object(exchange, "fetch_open_orders", return_value=[_open_leg(1)]), \
             mock.patch.object(exchange, "cancel_order",
                               side_effect=Exception('{"code":-2011,"msg":"Unknown order sent."}')):
            cancelled, failed = exchange.sweep_orphan_legs("BNBUSDT")
        self.assertEqual((cancelled, failed), (["1"], []))

    def test_real_cancel_failures_are_reported_not_raised(self):
        with mock.patch.object(exchange, "fetch_open_orders",
                               return_value=[_open_leg(1), _open_leg(2)]), \
             mock.patch.object(exchange, "cancel_order",
                               side_effect=[Exception("timeout"), None]):
            cancelled, failed = exchange.sweep_orphan_legs("BNBUSDT")
        self.assertEqual((cancelled, failed), (["2"], ["1"]))


class TestCloseSweepsProtection(unittest.TestCase):
    def test_close_cancels_the_stranded_legs(self):
        closed = dict(ENTRY, side="sell", reduceOnly=True)
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "close_position", return_value=closed), \
             mock.patch.object(perp.exchange, "sweep_orphan_legs",
                               return_value=(["11", "22"], [])) as sweep:
            out = perp.close(perp.CloseReq(symbol="BNBUSDT"))
        self.assertTrue(out["ok"])
        self.assertEqual(out["cancelled_protection"], ["11", "22"])
        self.assertNotIn("cancel_failed", out)
        sweep.assert_called_once_with("BNBUSDT")

    def test_sweep_failure_does_not_fail_the_close(self):
        closed = dict(ENTRY, side="sell", reduceOnly=True)
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "close_position", return_value=closed), \
             mock.patch.object(perp.exchange, "sweep_orphan_legs",
                               side_effect=Exception("listing down")):
            out = perp.close(perp.CloseReq(symbol="BNBUSDT"))
        self.assertTrue(out["ok"])                    # the flatten DID happen
        self.assertIn("protection_sweep_error", out)


class TestProtectionTriggerGuard(unittest.TestCase):
    def test_in_zone_stop_rejected_before_any_leg_is_placed(self):
        # stop_loss 800 ≥ mark 720 on a long → 400, and the VALID take_profit in the
        # same request must not be half-applied first.
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "fetch_positions", return_value=[POSITION_ROW]), \
             mock.patch.object(perp.exchange, "place_stop") as ps:
            with self.assertRaises(HTTPException) as ctx:
                perp.set_protection(perp.ProtectionReq(symbol="BNBUSDT",
                                                       take_profit=755.0, stop_loss=800.0))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("immediately", ctx.exception.detail)
        ps.assert_not_called()

    def test_in_zone_take_profit_rejected(self):
        # take_profit 700 ≤ mark 720 on a long is already in the fire zone.
        with mock.patch.object(perp, "require_trade_key", lambda: None), \
             mock.patch.object(perp.exchange, "fetch_positions", return_value=[POSITION_ROW]), \
             mock.patch.object(perp.exchange, "place_stop") as ps:
            with self.assertRaises(HTTPException) as ctx:
                perp.set_protection(perp.ProtectionReq(symbol="BNBUSDT", take_profit=700.0))
        self.assertEqual(ctx.exception.status_code, 400)
        ps.assert_not_called()


if __name__ == "__main__":
    unittest.main()
