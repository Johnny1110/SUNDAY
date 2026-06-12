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
