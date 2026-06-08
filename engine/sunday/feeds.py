"""Information layer — batch-1 perp microstructure feeds (milestone-4 T1).

Sunday ingests funding / open-interest / basis per symbol from the exchange and
writes them to `perp_metrics` (modeling-grade). The agents reason over these
*normalised features* (via `/desk`) instead of raw firehose — controlling token
cost + quality, and giving the research desk the quant-credible signals that are
real edges on perps (funding reflexivity, OI build-up, basis stretch).

`derive_metrics` is PURE (annualisation + basis math), unit-testable without an
exchange. `fetch_perp_metrics`/`ingest_all` are the live edge (call exchange +
store). Liquidations / long-short ratio columns exist in the schema but are
best-effort None in batch-1 (testnet rarely serves them; wired for later/batch-2).
"""

from __future__ import annotations

import logging

# exchange (ccxt) / store (redis+psycopg) are imported lazily inside the live-edge
# functions so the PURE `derive_metrics` stays importable + unit-testable stdlib-only.

log = logging.getLogger("sunday")

# Binance USDⓈ-M funding settles every 8h → 3×/day.
_FUNDING_PER_DAY = 3


def derive_metrics(symbol: str, rate: float | None, mark: float | None,
                   index: float | None, oi: float | None) -> dict:
    """Pure: assemble a perp_metrics row from raw reads (annualise funding, compute basis)."""
    annual = round(rate * _FUNDING_PER_DAY * 365 * 100.0, 2) if rate is not None else None
    basis_bps = round((mark - index) / index * 10_000.0, 2) if (mark and index) else None
    return {
        "symbol": symbol,
        "funding_rate": rate,
        "funding_annual_pct": annual,
        "open_interest": oi,
        "long_short_ratio": None,   # best-effort (batch-2 / 3rd-party)
        "basis_bps": basis_bps,
        "liq_long_usd": None,
        "liq_short_usd": None,
    }


def fetch_perp_metrics(symbol: str) -> dict:
    """Live: read the exchange and derive one perp_metrics row for `symbol`."""
    from . import exchange
    fi = exchange.fetch_funding_info(symbol)
    oi = exchange.fetch_open_interest(symbol)
    return derive_metrics(symbol, fi["rate"], fi["mark"], fi["index"], oi)


def ingest_all(symbols: list[str]) -> int:
    """Fetch + persist perp_metrics for the whole basket. Never raises (a feed hiccup
    must not kill the watcher); returns how many rows were written."""
    from . import store
    n = 0
    for sym in symbols:
        try:
            m = fetch_perp_metrics(sym)
            store.record_perp_metrics(m)
            n += 1
        except Exception as e:  # one symbol's feed failing must not stop the rest
            log.warning("feed ingest %s: %s", sym, e)
    return n
