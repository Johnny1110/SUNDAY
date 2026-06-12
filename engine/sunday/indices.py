"""External macro/crypto indices (req 4).

The "risk weather" an agent reads alongside the order book: crypto Fear & Greed +
BTC dominance (crypto-native sentiment), and VIX / DXY / S&P500 / Nasdaq / US10Y /
Gold / Brent crude (the macro backdrop crypto tracks). All free, no key.

  * crypto sentiment — alternative.me (F&G), CoinGecko /global (dominance/mcap)
  * macro/equities   — Stooq CSV, with a Yahoo Finance chart fallback per symbol

stdlib only (urllib/csv/json — matches events.py, no httpx). Pure parsers are split
from the network/cache layer so they unit-test against sample payloads. Each value is
TTL-cached and served **stale-on-error** rather than failing — an index feed hiccup
must never 500 an agent mid-decision.
"""

from __future__ import annotations

import csv
import io
import json
import ssl
import time
import urllib.request

# key -> (group, human label, ttl bucket)
_SPEC: dict[str, tuple[str, str, str]] = {
    "fear-greed":    ("crypto",   "Crypto Fear & Greed", "feargreed"),
    "btc-dominance": ("crypto",   "BTC Dominance",       "fast"),
    "vix":           ("macro",    "VIX",                 "macro"),
    "dxy":           ("macro",    "US Dollar Index (DXY)", "macro"),
    "spx":           ("equities", "S&P 500",             "macro"),
    "ndx":           ("equities", "Nasdaq 100",          "macro"),
    "us10y":         ("rates",    "US 10Y Yield",        "macro"),
    "gold":          ("metals",   "Gold (XAU/USD)",      "macro"),
    "oil":           ("energy",   "Brent Crude Oil",     "macro"),
}
INDEX_KEYS = list(_SPEC.keys())

_TTL_ATTR = {"feargreed": "indices_ttl_feargreed", "fast": "indices_ttl_fast", "macro": "indices_ttl_macro"}

_STOOQ = {"vix": "^vix", "dxy": "^dxy", "spx": "^spx", "ndx": "^ndx", "us10y": "10us.b", "gold": "xauusd",
          "oil": "cb.f"}   # Brent crude futures — the international oil benchmark (WTI would be cl.f)
_YAHOO = {"vix": "^VIX", "dxy": "DX-Y.NYB", "spx": "^GSPC", "ndx": "^NDX", "us10y": "^TNX", "gold": "GC=F",
          "oil": "BZ=F"}

_cache: dict[str, tuple[float, dict]] = {}


def _f(v) -> float | None:
    try:
        return float(v) if v not in (None, "", "N/D") else None
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    f = _f(v)
    return int(f) if f is not None else None


# --------------------------------------------------------------------------
# Pure parsers (unit-tested against sample payloads)
# --------------------------------------------------------------------------

def parse_fear_greed(payload: dict) -> dict:
    """alternative.me /fng/ → {value, classification, ts}."""
    d = (payload.get("data") or [{}])[0]
    return {"value": _int(d.get("value")), "classification": d.get("value_classification"),
            "ts": _int(d.get("timestamp"))}


def parse_coingecko_global(payload: dict) -> dict:
    """CoinGecko /global → dominance + total market cap/volume."""
    d = payload.get("data") or {}
    mcap_pct = d.get("market_cap_percentage") or {}
    return {
        "btc_dominance": _f(mcap_pct.get("btc")),
        "eth_dominance": _f(mcap_pct.get("eth")),
        "total_market_cap_usd": _f((d.get("total_market_cap") or {}).get("usd")),
        "total_volume_usd": _f((d.get("total_volume") or {}).get("usd")),
        "market_cap_change_24h_pct": _f(d.get("market_cap_change_percentage_24h_usd")),
    }


def parse_stooq_csv(text: str) -> dict | None:
    """Stooq l/ CSV (Symbol,Date,Time,Open,High,Low,Close,Volume) → normalized quote."""
    row = next(csv.DictReader(io.StringIO(text.strip())), None)
    if not row:
        return None
    close, open_ = _f(row.get("Close")), _f(row.get("Open"))
    if close is None:
        return None
    change = ((close - open_) / open_ * 100) if (open_ and open_ != 0) else None
    return {"price": close, "open": open_, "high": _f(row.get("High")), "low": _f(row.get("Low")),
            "change_pct": round(change, 3) if change is not None else None,
            "date": row.get("Date"), "time": row.get("Time")}


def parse_yahoo_chart(payload: dict) -> dict | None:
    """Yahoo v8 chart → {price, prev_close, change_pct} from meta (fallback feed)."""
    res = (((payload.get("chart") or {}).get("result")) or [None])[0]
    if not res:
        return None
    meta = res.get("meta") or {}
    price = _f(meta.get("regularMarketPrice"))
    prev = _f(meta.get("chartPreviousClose") or meta.get("previousClose"))
    if price is None:
        return None
    change = ((price - prev) / prev * 100) if (prev and prev != 0) else None
    return {"price": price, "prev_close": prev,
            "change_pct": round(change, 3) if change is not None else None}


# --------------------------------------------------------------------------
# Network + cache
# --------------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    """A verified TLS context. urllib's default trusts the system CA store, which is
    missing/stale in some Python installs (→ CERTIFICATE_VERIFY_FAILED on every feed).
    Prefer certifi's bundle (already present — it's what ccxt's HTTP stack uses); fall
    back to the platform default if certifi is somehow absent."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL = _ssl_context()


def _get_json(url: str, timeout: float = 6.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "sunday/0.6"})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_text(url: str, timeout: float = 6.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "sunday/0.6"})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
        return r.read().decode("utf-8")


def _fetch_traditional(key: str) -> dict | None:
    """Stooq first (cheap CSV); Yahoo chart as fallback if Stooq is N/D or down."""
    try:
        d = parse_stooq_csv(_get_text(f"https://stooq.com/q/l/?s={_STOOQ[key]}&f=sd2t2ohlcv&h&e=csv"))
        if d and d.get("price") is not None:
            return {**d, "source": "stooq"}
    except Exception:
        pass
    d = parse_yahoo_chart(_get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{_YAHOO[key]}"))
    return {**d, "source": "yahoo"} if d else None


def _fetch(key: str) -> dict | None:
    if key == "fear-greed":
        return parse_fear_greed(_get_json("https://api.alternative.me/fng/?limit=1"))
    if key == "btc-dominance":
        g = parse_coingecko_global(_get_json("https://api.coingecko.com/api/v3/global"))
        return {"value": g.get("btc_dominance"), "unit": "%", **g}
    return _fetch_traditional(key)


def _ttl(bucket: str) -> int:
    from .config import settings  # lazy: keep this module importable without pydantic (tests)
    return int(getattr(settings, _TTL_ATTR[bucket]))


def _wrap(key: str, data: dict | None, now: float) -> dict:
    group, label, _ = _SPEC[key]
    return {"key": key, "group": group, "label": label,
            "available": data is not None, "as_of": int(now * 1000), **(data or {})}


def get_index(key: str) -> dict:
    """One index, TTL-cached. Serves the last good value (``stale: true``) on a feed
    failure; only returns ``available: false`` when there's nothing cached at all."""
    if key not in _SPEC:
        raise KeyError(key)
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _ttl(_SPEC[key][2]):
        return {**hit[1], "stale": False}
    try:
        wrapped = _wrap(key, _fetch(key), now)
        _cache[key] = (now, wrapped)
        return {**wrapped, "stale": False}
    except Exception as e:
        if hit:
            return {**hit[1], "stale": True}
        group, label, _ = _SPEC[key]
        return {"key": key, "group": group, "label": label, "available": False,
                "error": f"{type(e).__name__}: {str(e)[:120]}"}


def get_all() -> list[dict]:
    """Every index (each cached independently)."""
    return [get_index(k) for k in INDEX_KEYS]
