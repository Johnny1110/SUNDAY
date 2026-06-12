"""Unit tests for the external-index parsers (sample payloads, no network)."""

import unittest

from sunday import indices as X


class TestParsers(unittest.TestCase):
    def test_fear_greed(self):
        d = X.parse_fear_greed({"data": [{"value": "72", "value_classification": "Greed", "timestamp": "1717000000"}]})
        self.assertEqual(d["value"], 72)
        self.assertEqual(d["classification"], "Greed")

    def test_fear_greed_empty(self):
        self.assertIsNone(X.parse_fear_greed({})["value"])

    def test_coingecko(self):
        d = X.parse_coingecko_global({"data": {"market_cap_percentage": {"btc": 54.2, "eth": 17.1},
                                                "total_market_cap": {"usd": 2.4e12}}})
        self.assertAlmostEqual(d["btc_dominance"], 54.2)
        self.assertAlmostEqual(d["total_market_cap_usd"], 2.4e12)

    def test_stooq(self):
        d = X.parse_stooq_csv("Symbol,Date,Time,Open,High,Low,Close,Volume\n"
                              "^SPX,2026-06-09,21:00:00,5400,5421,5390,5418,0")
        self.assertAlmostEqual(d["price"], 5418.0)
        self.assertAlmostEqual(d["change_pct"], 0.333, places=3)

    def test_stooq_no_data(self):
        self.assertIsNone(X.parse_stooq_csv("Symbol,Date,Time,Open,High,Low,Close,Volume\n"
                                            "^DXY,N/D,N/D,N/D,N/D,N/D,N/D,N/D"))

    def test_yahoo(self):
        d = X.parse_yahoo_chart({"chart": {"result": [{"meta": {"regularMarketPrice": 17.3, "chartPreviousClose": 18.0}}]}})
        self.assertAlmostEqual(d["price"], 17.3)
        self.assertLess(d["change_pct"], 0.0)

    def test_yahoo_empty(self):
        self.assertIsNone(X.parse_yahoo_chart({"chart": {"result": []}}))


class TestSpecConsistency(unittest.TestCase):
    def test_oil_registered(self):
        """Brent crude — the international oil benchmark — is on the panel."""
        self.assertIn("oil", X.INDEX_KEYS)
        group, label, bucket = X._SPEC["oil"]
        self.assertEqual(group, "energy")
        self.assertIn("Brent", label)
        self.assertEqual(bucket, "macro")
        self.assertEqual(X._STOOQ["oil"], "cb.f")
        self.assertEqual(X._YAHOO["oil"], "BZ=F")

    def test_every_traditional_key_has_both_feeds(self):
        """Every non-crypto index must map on BOTH feeds — Stooq is primary,
        Yahoo is the fallback get_index relies on when Stooq is down."""
        for key, (group, _, _) in X._SPEC.items():
            if group == "crypto":
                continue
            self.assertIn(key, X._STOOQ, f"{key} missing a Stooq symbol")
            self.assertIn(key, X._YAHOO, f"{key} missing a Yahoo symbol")


if __name__ == "__main__":
    unittest.main()
