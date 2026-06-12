"""Binance USDⓈ-M adapter — the proxy's spine (milestone-6).

Two ccxt instances, on purpose (the locked topology decision):

  * ``market_ex()`` — **mainnet**, public, no key. All read-only market data
    (tickers, klines, funding, OI) goes here so agents decide on REAL prices.
  * ``trade_ex()``  — **testnet** (``set_sandbox_mode``), keyed. All account +
    order flow goes here so execution is fake-money-safe.

Both are ``ccxt.binanceusdm`` with ``defaultType: future``. Symbol normalization is
per-instance (markets are loaded lazily on first use). Every function is a thin pass
to ccxt; the HTTP layer wraps these in a clean-502 helper.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import ccxt

from . import algoorders
from .config import settings

_market: ccxt.binanceusdm | None = None
_trade: ccxt.binanceusdm | None = None


def market_ex() -> ccxt.binanceusdm:
    """Mainnet, public (no key) — source of truth for market data."""
    global _market
    if _market is None:
        _market = ccxt.binanceusdm({
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
    return _market


def trade_ex() -> ccxt.binanceusdm:
    """Testnet (sandbox), keyed — source of truth for the account/book."""
    global _trade
    if _trade is None:
        ex = ccxt.binanceusdm({
            "apiKey": settings.binance_testnet_key or None,
            "secret": settings.binance_testnet_secret or None,
            "enableRateLimit": True,
            # warnOnFetchOpenOrdersWithoutSymbol: ack ccxt's rate-limit warning so
            # GET /api/account/orders/open (no symbol) returns instead of raising.
            # (enableRateLimit already throttles; the symbol-less call just costs more weight.)
            # adjustForTimeDifference: ccxt signs with its own millisecond nonce and does NOT
            # correct clock skew by default → a fast local clock trips Binance -1021 on every
            # signed write (orders/leverage/margin). Turn it on so ccxt syncs to server time,
            # and widen recvWindow to match the raw _signed path below.
            "options": {"defaultType": "future", "disableFuturesSandboxWarning": True,
                        "warnOnFetchOpenOrdersWithoutSymbol": False,
                        "adjustForTimeDifference": True, "recvWindow": _RECV_WINDOW},
        })
        ex.set_sandbox_mode(True)
        _trade = ex
    return _trade


def _sym(ex: ccxt.binanceusdm, symbol: str) -> str:
    """Map an exchange id (BTCUSDT) to ccxt's unified symbol (BTC/USDT:USDT)."""
    if not ex.markets:
        ex.load_markets()
    if symbol in ex.markets:
        return symbol
    m = ex.markets_by_id.get(symbol)
    if m:
        return (m[0] if isinstance(m, list) else m)["symbol"]
    return symbol


def unify(symbol: str) -> str:
    """Public: id → unified symbol on the MARKET (mainnet) instance."""
    return _sym(market_ex(), symbol)


def unify_trade(symbol: str) -> str:
    """Public: id → unified symbol on the TRADE (testnet) instance — used to match
    a user-supplied id against position/order rows from the testnet book."""
    return _sym(trade_ex(), symbol)


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Market data (mainnet)
# --------------------------------------------------------------------------

def is_perp(market: dict) -> bool:
    """A tradeable USDⓈ-M linear perpetual (what /api/markets lists)."""
    return bool(
        market.get("swap") and market.get("linear") and market.get("active")
        and market.get("quote") in ("USDT", "USDC")
    )


def list_perp_tickers() -> list[dict]:
    """All USDⓈ-M perpetual tickers (mainnet), normalized for the markets table.

    One ``fetch_tickers`` call (callers cache it). Each row: id + last + 24h
    volume/change/high/low — exactly the fields the markets list sorts/filters on.
    """
    ex = market_ex()
    if not ex.markets:
        ex.load_markets()
    tickers = ex.fetch_tickers()
    out: list[dict] = []
    for unified, t in tickers.items():
        mkt = ex.markets.get(unified)
        if not mkt or not is_perp(mkt):
            continue
        out.append({
            "symbol": mkt["id"],                       # BTCUSDT (what agents pass back)
            "unified": unified,
            "last": _f(t.get("last")),
            "high": _f(t.get("high")),
            "low": _f(t.get("low")),
            "change_pct": _f(t.get("percentage")),     # 24h % change
            "quote_volume": _f(t.get("quoteVolume")),  # 24h USDT volume
            "base_volume": _f(t.get("baseVolume")),
        })
    return out


def fetch_ticker(symbol: str) -> dict:
    return market_ex().fetch_ticker(unify(symbol))


def market_info(symbol: str) -> dict:
    """Static market metadata (precision / limits / leverage cap) for one symbol."""
    ex = market_ex()
    if not ex.markets:
        ex.load_markets()
    m = ex.markets.get(unify(symbol)) or {}
    return {
        "symbol": m.get("id"),
        "unified": m.get("symbol"),
        "base": m.get("base"),
        "quote": m.get("quote"),
        "active": m.get("active"),
        "contract_size": m.get("contractSize"),
        "precision": m.get("precision"),
        "limits": m.get("limits"),
        "max_leverage": (m.get("limits", {}).get("leverage", {}) or {}).get("max"),
        "taker": m.get("taker"),
        "maker": m.get("maker"),
    }


def fetch_ohlcv(symbol: str, tf: str = "1h", limit: int = 200, since: int | None = None) -> list[list]:
    return market_ex().fetch_ohlcv(unify(symbol), timeframe=tf, since=since, limit=limit)


def fetch_funding_rate(symbol: str) -> dict:
    """Current funding (per-8h fraction) + mark/index + next funding time — normalized."""
    fr = market_ex().fetch_funding_rate(unify(symbol))
    return {
        "symbol": symbol,
        "rate": _f(fr.get("fundingRate")),
        "mark": _f(fr.get("markPrice")),
        "index": _f(fr.get("indexPrice")),
        "next_funding_ts": fr.get("fundingTimestamp") or fr.get("nextFundingTime"),
        "interval_hours": fr.get("interval"),
    }


def fetch_funding_history(symbol: str, since: int | None = None, limit: int = 100) -> list[dict]:
    rows = market_ex().fetch_funding_rate_history(unify(symbol), since=since, limit=limit)
    return [{"symbol": symbol, "ts": r.get("timestamp"),
             "rate": _f(r.get("fundingRate"))} for r in rows]


def fetch_open_interest(symbol: str) -> float | None:
    try:
        oi = market_ex().fetch_open_interest(unify(symbol))
        return _f(oi.get("openInterestValue")) or _f(oi.get("openInterestAmount"))
    except Exception:
        return None


def supported_timeframes() -> list[str]:
    ex = market_ex()
    return list((ex.timeframes or {}).keys())


# --------------------------------------------------------------------------
# Account + orders (testnet) — direct signed REST
#
# ccxt's binanceusdm position parse drops `leverage` (and is lossy elsewhere), so the
# account READS go straight to Binance's fapi and return the RAW exchange shapes — full
# control over every field (leverage / liquidationPrice / marginType / stopPrice …).
# Order WRITES stay on ccxt below (precision helpers + the tested create_order path).
# HMAC-signed, stdlib only; TLS via certifi (some Python installs lack a usable system
# CA store → CERTIFICATE_VERIFY_FAILED).
# --------------------------------------------------------------------------

_TESTNET = "https://testnet.binancefuture.com"


def _ssl_ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL = _ssl_ctx()

# Binance's timestamp rule is ASYMMETRIC: a request's `timestamp` may be at most 1000ms
# AHEAD of server time, but up to `recvWindow` BEHIND it. So we (a) estimate the clock
# offset with a round-trip midpoint (kills most of the network-latency bias that, through
# a slow proxy, pushes the estimate >1000ms ahead and trips -1021 every poll), and (b)
# deliberately aim the timestamp slightly BEHIND server time — being behind is the safe
# side. recvWindow is widened to give that behind-bias room.
_RECV_WINDOW = 10000
_TS_SAFETY_MS = 1000              # bias the request timestamp this far behind server time
_CLOCK_TTL = 300.0               # re-sync the offset at most this often (seconds)
_clock = {"offset": 0.0, "at": 0.0, "synced": False}


def _sync_clock() -> None:
    """Re-measure the Binance↔local offset via a round-trip midpoint (removes ~half the
    one-way latency bias of a naive serverTime − localBefore)."""
    before = time.time()
    with urllib.request.urlopen(_TESTNET + "/fapi/v1/time", timeout=10, context=_SSL) as r:
        srv = json.loads(r.read())["serverTime"]
    after = time.time()
    _clock["offset"] = srv - (before + after) / 2.0 * 1000.0
    _clock["at"] = after
    _clock["synced"] = True


def _server_ms() -> int:
    """Server-aligned ms for signing, biased to sit just BEHIND server time so a fast local
    clock can't trip -1021. Offset is cached `_CLOCK_TTL`; a sync failure degrades to the
    last offset rather than crashing the poll loop."""
    now = time.time()
    if not _clock["synced"] or now - _clock["at"] > _CLOCK_TTL:
        try:
            _sync_clock()
        except Exception:
            pass  # keep serving on the previous offset; a -1021 below forces a retry-resync
    return int(time.time() * 1000.0 + _clock["offset"]) - _TS_SAFETY_MS


def clock_info() -> dict:
    """Binance↔local clock state for /api/system/time: last measured offset (ms,
    0 until the first sync) and whether a sync has succeeded yet."""
    return {"offset_ms": round(_clock["offset"]), "synced": _clock["synced"]}


def _signed_request(path: str, params: dict | None = None):
    """One signed GET to the testnet fapi. Raises with Binance's {code,msg} on failure."""
    p = {k: v for k, v in (params or {}).items() if v is not None}
    p["timestamp"] = _server_ms()
    p["recvWindow"] = _RECV_WINDOW
    qs = urllib.parse.urlencode(p)
    sig = hmac.new(settings.binance_testnet_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{_TESTNET}{path}?{qs}&signature={sig}",
                                 headers={"X-MBX-APIKEY": settings.binance_testnet_key})
    try:
        with urllib.request.urlopen(req, timeout=12, context=_SSL) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # surface Binance's {code,msg}, not a bare 500
        raise RuntimeError(f"binance {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")


def _signed(path: str, params: dict | None = None):
    """Signed GET with one self-healing retry: a -1021 (timestamp out of window) forces a
    fresh clock sync and a single re-sign, so transient drift recovers on its own."""
    try:
        return _signed_request(path, params)
    except RuntimeError as e:
        if "-1021" not in str(e):
            raise
        _clock["synced"] = False     # force _server_ms() to re-sync before the retry
        return _signed_request(path, params)


def fetch_positions() -> list[dict]:
    """Open positions — raw positionRisk rows (leverage + liquidationPrice included)."""
    return [p for p in _signed("/fapi/v2/positionRisk") if _f(p.get("positionAmt"))]


def leverage_by_symbol() -> dict[str, int]:
    """symbol id -> configured leverage, from positionRisk (annotates open orders)."""
    out: dict[str, int] = {}
    for p in _signed("/fapi/v2/positionRisk"):
        lev = _f(p.get("leverage"))
        if lev:
            out[p["symbol"]] = int(lev)
    return out


def fetch_open_algo_orders(symbol: str | None = None) -> list[dict]:
    """Untriggered conditional (TP/SL/trailing) legs from the Algo Service, reshaped to
    the legacy openOrders row shape with ``algo: True`` (see algoorders.py)."""
    rows = _signed("/fapi/v1/openAlgoOrders", {"symbol": symbol.upper()} if symbol else None)
    return [algoorders.normalize_algo_order(a) for a in rows]


def fetch_open_orders(symbol: str | None = None) -> list[dict]:
    """Open orders = regular book + untriggered conditional legs. Since Binance's
    2025-12-09 Algo-Service migration, /fapi/v1/openOrders alone misses TP/SL legs —
    a failure on either read raises (a silent partial list would re-create the
    false-naked-position bug this merge fixes, PRD-003)."""
    plain = _signed("/fapi/v1/openOrders", {"symbol": symbol.upper()} if symbol else None)
    return plain + fetch_open_algo_orders(symbol)


def fetch_orders(symbol: str, since: int | None = None, limit: int = 100) -> list[dict]:
    """Order history (Binance fapi requires a symbol), merged across both books.

    /fapi/v1/allAlgoOrders rejects startTime windows ≥ 7 days, so ``since`` is applied
    locally to the algo rows instead (their lookback is bounded by ``limit``); the
    regular book keeps server-side startTime. Rows stay ascending by time."""
    lim = min(limit, 1000)
    plain = _signed("/fapi/v1/allOrders", {"symbol": symbol.upper(), "startTime": since, "limit": lim})
    algo = [algoorders.normalize_algo_order(a) for a in
            _signed("/fapi/v1/allAlgoOrders", {"symbol": symbol.upper(), "limit": lim})]
    if since:
        algo = [a for a in algo if (a.get("time") or 0) >= since]
    return sorted(plain + algo, key=lambda o: o.get("time") or 0)


def fetch_my_trades(symbol: str, since: int | None = None, limit: int = 100) -> list[dict]:
    """Fill history (Binance fapi requires a symbol)."""
    return _signed("/fapi/v1/userTrades", {"symbol": symbol.upper(), "startTime": since, "limit": min(limit, 1000)})


def fetch_account() -> dict:
    """Raw /fapi/v2/account — wallet / margin balances + totals."""
    return _signed("/fapi/v2/account")


def amount_to_precision(symbol: str, amount: float) -> float:
    return float(trade_ex().amount_to_precision(unify_trade(symbol), amount))


def price_to_precision(symbol: str, price: float) -> float:
    return float(trade_ex().price_to_precision(unify_trade(symbol), price))


def set_leverage(symbol: str, leverage: int) -> None:
    trade_ex().set_leverage(int(leverage), unify_trade(symbol))


def set_margin_mode(symbol: str, mode: str) -> None:
    """mode: 'isolated' (逐倉) | 'cross' (全倉)."""
    trade_ex().set_margin_mode(mode.lower(), unify_trade(symbol))


def create_order(symbol: str, type_: str, side: str, amount: float,
                 price: float | None = None, params: dict | None = None) -> dict:
    return trade_ex().create_order(unify_trade(symbol), type_, side, amount, price, params or {})


def fetch_mark_price(symbol: str) -> float | None:
    """TESTNET mark price (premiumIndex, public) — the reference a MARK_PRICE trigger
    leg is judged against at placement. None when unavailable: callers treat that as
    "cannot judge" and proceed (the workingType fix below is the primary guard)."""
    try:
        url = f"{_TESTNET}/fapi/v1/premiumIndex?symbol={symbol.upper()}"
        with urllib.request.urlopen(url, timeout=8, context=_SSL) as r:
            return _f(json.loads(r.read()).get("markPrice"))
    except Exception:
        return None


def place_stop(symbol: str, close_side: str, qty: float, trigger_price: float,
               take_profit: bool = False) -> dict:
    """A reduce-only trigger leg: TAKE_PROFIT_MARKET (tp) or STOP_MARKET (sl).

    ``close_side`` is opposite the position side. Used to attach TP/SL to an entry.

    workingType=MARK_PRICE is load-bearing (BUG-01/BUG-04): the default CONTRACT_PRICE
    judges the trigger on the TESTNET *last-traded* price, which drifts far from the
    mainnet prices agents decide on (thin testnet books) — a stop placed safely below
    the real mark could already sit in its fire zone on testnet last and execute the
    moment it lands as a reduce-only market close. The testnet MARK price is
    index-derived and tracks the real market, so triggers fire where agents expect.
    """
    order_type = "TAKE_PROFIT_MARKET" if take_profit else "STOP_MARKET"
    return trade_ex().create_order(
        unify_trade(symbol), order_type, close_side, qty,
        params={"stopPrice": trigger_price, "reduceOnly": True, "workingType": "MARK_PRICE"},
    )


def cancel_order(order_id: str, symbol: str, algo: bool = False) -> dict:
    """Cancel one order in whichever book holds it. TP/SL legs live in the Algo Service
    (id = algoId): pass ``algo=True`` to go there directly, otherwise a regular-book
    -2011 (unknown order) transparently retries against the algo book — so agents can
    cancel any id from /api/account/orders/open without caring which book it sits in."""
    sym = unify_trade(symbol)
    if algo:
        return trade_ex().cancel_order(order_id, sym, params={"trigger": True})
    try:
        return trade_ex().cancel_order(order_id, sym)
    except Exception as e:
        if not algoorders.is_unknown_order(str(e)):
            raise
        return trade_ex().cancel_order(order_id, sym, params={"trigger": True})


def cancel_all_orders(symbol: str) -> None:
    """Cancel a symbol's resting orders in BOTH books — regular and algo. Clearing only
    the regular book strands TP/SL legs as orphans that block margin-mode changes
    with -4047 (PRD-003)."""
    sym = unify_trade(symbol)
    trade_ex().cancel_all_orders(sym)
    trade_ex().cancel_all_orders(sym, params={"trigger": True})


def close_position(symbol: str) -> dict | None:
    """Flatten one symbol with a reduce-only market order. None if no open position.

    Reads the raw positionRisk shape (positionAmt: signed string); writes via ccxt."""
    sym = symbol.upper()
    for p in fetch_positions():
        amt = _f(p.get("positionAmt"))
        if p.get("symbol") == sym and amt:
            close_side = "sell" if amt > 0 else "buy"
            return create_order(symbol, "market", close_side, abs(amt), params={"reduceOnly": True})
    return None


def cancel_all_open_orders() -> list[str]:
    """Cancel every resting order across all symbols (test reset). Returns the symbols cleared."""
    symbols = sorted({o["symbol"] for o in fetch_open_orders() if o.get("symbol")})
    for sym in symbols:
        cancel_all_orders(sym)
    return symbols


def close_all_positions() -> list[str]:
    """Flatten every open position with reduce-only market orders (test reset).
    Returns the symbols closed. Cancel resting orders BEFORE calling this so the
    TP/SL legs don't reject as the position goes flat."""
    closed: list[str] = []
    for p in fetch_positions():
        amt = _f(p.get("positionAmt"))
        sym = p.get("symbol")
        if sym and amt:
            close_side = "sell" if amt > 0 else "buy"
            create_order(sym, "market", close_side, abs(amt), params={"reduceOnly": True})
            closed.append(sym)
    return closed
