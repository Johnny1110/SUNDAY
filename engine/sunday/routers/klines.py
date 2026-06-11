"""/api/klines — OHLCV + technical indicators (req 2).

Klines for any Binance timeframe (param-switched), plus a derived-indicator panel
computed over the same candles via ``indicators.py`` — so an agent reads exactly the
numbers a chart would show, without shipping raw OHLCV through an LLM to compute RSI.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import exchange, indicators
from ..apiutil import ex_call
from ..market import Candles
from ..ttlcache import StaleCache

router = APIRouter(prefix="/api/klines", tags=["klines"])

# Binance USDⓈ-M timeframes (validated again against ccxt at call time).
INTERVALS = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h",
             "12h", "1d", "3d", "1w", "1M")

_SEC = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
        "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200,
        "1d": 86400, "3d": 259200, "1w": 604800, "1M": 2592000}

# Candles for the indicator panel, cached per (symbol, interval, limit) with a TTL
# scaled to the bar size (~1/20th, clamped 10s–5min: 5m→15s, 1h→3min, 4h+→5min) and
# served STALE when the upstream fails — a retry after an upstream stall answers
# instantly with the last good panel instead of timing out again (PRD-005).
_CANDLES = StaleCache()


def _ttl(interval: str) -> int:
    return min(max(_SEC.get(interval, 3600) // 20, 10), 300)


def _check_interval(interval: str) -> None:
    if interval not in INTERVALS:
        raise HTTPException(400, f"interval must be one of: {', '.join(INTERVALS)}")


@router.get("")
def klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 200,
           start: int | None = None, end: int | None = None) -> dict:
    """OHLCV candles. `interval` switches the timeframe; `limit` ≤ 1500; optional
    `start`/`end` are epoch-ms bounds (`start` pages forward from a time)."""
    _check_interval(interval)
    limit = max(1, min(limit, 1500))
    rows = ex_call(lambda: exchange.fetch_ohlcv(symbol, interval, limit, since=start))
    if end:
        rows = [r for r in rows if r[0] <= end]
    return {
        "symbol": symbol.upper(), "interval": interval,
        "columns": ["ts", "open", "high", "low", "close", "volume"],
        "count": len(rows), "ohlcv": rows,
    }


@router.get("/indicators")
def indicator_panel(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 200,
                    which: str = Query("rsi,ema,macd,bollinger,adx,atr", alias="set")) -> dict:
    """Computed indicators over the candles. `set` = comma list of
    rsi,ema,sma,macd,bollinger,adx,atr. Candles are briefly cached (interval-scaled
    TTL); if the upstream fails the last good data is served with `stale: true`."""
    _check_interval(interval)
    lim = max(limit, 200)
    rows, age, stale = ex_call(lambda: _CANDLES.get(
        (symbol.upper(), interval, lim), _ttl(interval),
        lambda: exchange.fetch_ohlcv(symbol, interval, lim)))
    c = Candles.from_klines(rows)
    want = {s.strip().lower() for s in which.split(",") if s.strip()}
    out: dict = {}
    if "ema" in want:
        out["ema"] = {"ema20": indicators.ema(c.closes, 20), "ema50": indicators.ema(c.closes, 50)}
    if "sma" in want:
        out["sma"] = {"sma20": indicators.sma(c.closes, 20), "sma50": indicators.sma(c.closes, 50)}
    if "rsi" in want:
        out["rsi"] = indicators.rsi(c.closes, 14)
    if "macd" in want:
        out["macd"] = indicators.macd(c.closes)
    if "bollinger" in want:
        out["bollinger"] = indicators.bollinger(c.closes, 20)
    if "adx" in want:
        out["adx"] = indicators.adx(c.highs, c.lows, c.closes, 14)
    if "atr" in want:
        out["atr"] = indicators.atr(c.highs, c.lows, c.closes, 14)
    return {
        "symbol": symbol.upper(), "interval": interval,
        "as_of": c.times[-1] if len(c) else None,
        "last_close": c.last_close, "indicators": out,
        # only on a degraded serve: upstream failed, this is the last good panel
        **({"stale": True, "stale_age_s": round(age, 1)} if stale else {}),
    }
