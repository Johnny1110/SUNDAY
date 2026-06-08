"""Unit tests for exchange signing (the security-critical, key-free part).

Live HTTP (orders/positions) needs real testnet credentials + network and is
integration-tested in the user's environment, not here.
"""

import unittest

from sunday import exchange

# Binance's own documented signed-request example (authoritative external vector).
_DOC_SECRET = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0"
_DOC_QUERY = ("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1"
             "&price=0.1&recvWindow=5000&timestamp=1499827319559")
_DOC_SIG = "b89008e7051ffbf2242be7dc5ae67fd146e6430688627b802c0cbec146e46aef"


class TestSigning(unittest.TestCase):
    def test_sign_matches_binance_documented_vector(self):
        self.assertEqual(exchange.sign(_DOC_QUERY, _DOC_SECRET), _DOC_SIG)

    def test_build_signed_query_reproduces_doc_vector(self):
        # params in the documented order; build appends recvWindow + timestamp + signature.
        params = {"symbol": "LTCBTC", "side": "BUY", "type": "LIMIT",
                  "timeInForce": "GTC", "quantity": 1, "price": 0.1}
        signed = exchange.build_signed_query(params, _DOC_SECRET, timestamp=1499827319559, recv_window=5000)
        self.assertEqual(signed, _DOC_QUERY + "&signature=" + _DOC_SIG)

    def test_signed_query_appends_required_fields(self):
        signed = exchange.build_signed_query({"symbol": "BTCUSDT"}, "secret", timestamp=123, recv_window=5000)
        self.assertIn("symbol=BTCUSDT", signed)
        self.assertIn("recvWindow=5000", signed)
        self.assertIn("timestamp=123", signed)
        self.assertIn("&signature=", signed)
        # signature is the hmac of everything before it
        body, sig = signed.rsplit("&signature=", 1)
        self.assertEqual(exchange.sign(body, "secret"), sig)


class TestAdapterShape(unittest.TestCase):
    def test_from_settings_and_base(self):
        class S:
            binance_testnet_key = "k"
            binance_testnet_secret = "s"
        ex = exchange.BinanceUSDM.from_settings(S())
        self.assertEqual(ex.key, "k")
        self.assertEqual(ex.base, exchange.TESTNET_BASE)


if __name__ == "__main__":
    unittest.main()
