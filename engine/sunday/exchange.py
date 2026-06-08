"""Binance USDⓈ-M (perpetual futures) testnet adapter — stdlib only.

PRD invariant: agents never hold exchange keys; only Sunday talks to the exchange.
This adapter is hand-rolled on ``urllib`` + ``hmac`` rather than ccxt — Gate-1 needs
a handful of endpoints, and a dependency-free adapter (a) keeps the engine light,
(b) makes the *signing* unit-testable against Binance's own documented vector, and
(c) lets public market data run anywhere. ccxt would return as a Gate-2 convenience
if the endpoint set grows.

The signing helpers (``sign`` / ``build_signed_query``) are module-level pure
functions so the security-critical part is verified in CI without a live key. The
``BinanceUSDM`` methods are thin HTTP shells (integration-tested in the user's env
with real testnet credentials; the auth path can't run in a sandbox).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .market import Candles

TESTNET_BASE = "https://testnet.binancefuture.com"


class ExchangeError(RuntimeError):
    """An exchange call failed; carries the HTTP status + body for the ledger."""

    def __init__(self, status: int | None, body: str):
        super().__init__(f"exchange error {status}: {body}")
        self.status = status
        self.body = body


def sign(query_string: str, secret: str) -> str:
    """HMAC-SHA256 hex of the query string, keyed by the API secret (Binance scheme)."""
    return hmac.new(secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()


def build_signed_query(params: dict, secret: str, timestamp: int, recv_window: int = 5000) -> str:
    """Append recvWindow + timestamp, then the signature over the exact sent string."""
    full = dict(params)
    full["recvWindow"] = recv_window
    full["timestamp"] = timestamp
    qs = urllib.parse.urlencode(full)
    return qs + "&signature=" + sign(qs, secret)


class BinanceUSDM:
    def __init__(self, key: str = "", secret: str = "", base: str = TESTNET_BASE,
                 recv_window: int = 5000, timeout: float = 10.0):
        self.key = key
        self.secret = secret
        self.base = base.rstrip("/")
        self.recv_window = recv_window
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings) -> "BinanceUSDM":
        return cls(settings.binance_testnet_key, settings.binance_testnet_secret)

    # --- transport ---------------------------------------------------------
    def _do(self, req: urllib.request.Request):
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace") if e.fp else ""
            raise ExchangeError(e.code, body) from e
        except urllib.error.URLError as e:
            raise ExchangeError(None, str(e.reason)) from e

    def _public_get(self, path: str, params: dict):
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        return self._do(urllib.request.Request(url, method="GET"))

    def _signed(self, method: str, path: str, params: dict | None = None):
        qs = build_signed_query(params or {}, self.secret, int(time.time() * 1000), self.recv_window)
        url = f"{self.base}{path}?{qs}"
        return self._do(urllib.request.Request(url, method=method, headers={"X-MBX-APIKEY": self.key}))

    # --- market data (public; runnable without keys) -----------------------
    def fetch_klines(self, symbol: str, interval: str = "1h", limit: int = 100) -> Candles:
        raw = self._public_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        return Candles.from_klines(raw)

    # --- account / positions (signed) --------------------------------------
    def positions(self, symbol: str | None = None) -> list[dict]:
        params = {"symbol": symbol} if symbol else {}
        rows = self._signed("GET", "/fapi/v2/positionRisk", params)
        # Keep only non-zero positions; map to Sunday's shape.
        out = []
        for r in rows:
            amt = float(r.get("positionAmt", 0) or 0)
            if amt == 0:
                continue
            out.append({
                "symbol": r["symbol"],
                "side": "long" if amt > 0 else "short",
                "qty": abs(amt),
                "entry_price": float(r.get("entryPrice", 0) or 0),
                "mark": float(r.get("markPrice", 0) or 0),
                "upnl": float(r.get("unRealizedProfit", 0) or 0),
                "leverage": float(r.get("leverage", 0) or 0),
            })
        return out

    def set_leverage(self, symbol: str, leverage: int):
        return self._signed("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})

    # --- execution (signed) ------------------------------------------------
    def market_order(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> dict:
        params = {"symbol": symbol, "side": side.upper(), "type": "MARKET", "quantity": qty}
        if reduce_only:
            params["reduceOnly"] = "true"
        return self._signed("POST", "/fapi/v1/order", params)

    def stop_market(self, symbol: str, side: str, stop_price: float, qty: float) -> dict:
        """Exchange-native STOP_MARKET — survives Sunday going down (PRD §7.3)."""
        return self._signed("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": side.upper(), "type": "STOP_MARKET",
            "stopPrice": stop_price, "quantity": qty, "reduceOnly": "true",
        })

    def cancel_all(self, symbol: str) -> dict:
        return self._signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
