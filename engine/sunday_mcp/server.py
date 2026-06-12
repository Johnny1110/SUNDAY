"""FastMCP server assembly — the ONLY module that imports the `mcp` SDK (S7).

Tools are deliberately thin: input is already schema-validated by the SDK,
so a tool body is `client.call → shaping/errors pure functions → str`.
Read tools carry readOnlyHint annotations (no effect under the swarm's
bypass permission mode today; hygiene for any stricter future host).

Output budget (S3): page_size caps live HERE in the schemas (markets 20,
open/history orders 30, trades 50, funding history 30, klines 500 bars) —
that, plus one-line-per-row shaping, keeps every tool under the 60k-char
design budget. Don't raise a cap without extending the budget tests.
"""

from __future__ import annotations

import os
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__, client, errors, shaping

_READONLY = ToolAnnotations(readOnlyHint=True)

Interval = Literal["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h",
                   "12h", "1d", "3d", "1w", "1M"]
IndexKey = Literal["fear-greed", "btc-dominance", "vix", "dxy", "spx", "ndx",
                   "us10y", "gold"]


def _call(method: str, path: str, **kw) -> client.Reply:
    """client.call with the unreachable case mapped to a tool error (S6 hint)."""
    try:
        return client.call(method, path, **kw)
    except client.EngineUnreachable as e:
        raise ToolError(errors.UNREACHABLE_TEXT) from e


def _shaped(reply: client.Reply, shaper) -> str:
    if errors.is_error(reply):
        return errors.upstream_error_text(reply)
    return shaper(reply.json or {})


def build_server() -> FastMCP:
    port = int(os.environ.get("SUNDAY_MCP_PORT", "7780"))
    mcp = FastMCP("sunday", host="127.0.0.1", port=port)

    # ── liveness ──────────────────────────────────────────────────────────────

    @mcp.tool(annotations=_READONLY)
    def ping() -> str:
        """Sidecar liveness + Sunday engine reachability probe (no market data)."""
        eng = client.probe_health()
        state = (f"engine reachable ({eng['status']})" if eng["reachable"]
                 else "engine UNREACHABLE — check GET /health (RUNBOOK.md)")
        return f"sunday-mcp {__version__} ok · {state}"

    # ── markets (mainnet prices) ──────────────────────────────────────────────

    @mcp.tool(annotations=_READONLY)
    def markets_list(
        sort: Literal["volume", "change", "symbol", "last"] = "volume",
        order: Literal["desc", "asc"] = "desc",
        search: str | None = None,
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=20)] = 10,
    ) -> str:
        """Tradeable USDⓈ-M perp markets, one line each: SYMBOL last 24h% volume
        (mainnet prices). `search` filters by symbol substring (e.g. "BTC").
        Full API contract: resource sunday://manual."""
        q: dict = {"sort": sort, "order": order, "page": page, "page_size": page_size}
        if search:
            q["symbol"] = search
        return _shaped(_call("GET", "/api/markets", query=q), shaping.shape_markets)

    @mcp.tool(annotations=_READONLY)
    def market_get(symbol: str) -> str:
        """One market: live ticker + the numbers every order must respect —
        price/qty precision, min/max qty, min notional, max leverage, fees."""
        return _shaped(_call("GET", f"/api/markets/{symbol.upper()}"),
                       shaping.shape_market_detail)

    @mcp.tool(annotations=_READONLY)
    def klines(
        symbol: str,
        interval: Interval = "1h",
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        start: Annotated[int | None, Field(description="epoch-ms lower bound")] = None,
        end: Annotated[int | None, Field(description="epoch-ms upper bound")] = None,
    ) -> str:
        """OHLCV candles as CSV rows (ts,open,high,low,close,volume), oldest
        first, mainnet prices. For indicator values use the `indicators` tool
        instead of recomputing from raw candles."""
        q = {"symbol": symbol, "interval": interval, "limit": limit,
             "start": start, "end": end}
        return _shaped(_call("GET", "/api/klines", query=q), shaping.shape_klines)

    @mcp.tool(annotations=_READONLY)
    def indicators(
        symbol: str,
        interval: Interval = "1h",
        set: str = "rsi,ema,macd,bollinger,adx,atr",
        limit: Annotated[int, Field(ge=200, le=400)] = 200,
    ) -> str:
        """Current technical-indicator panel (latest values) over recent candles:
        rsi, ema(20/50), sma(20/50), macd, bollinger(+z), adx, atr — `set` is a
        comma list. A `⚠ stale` first line means the engine served last-good
        data after an upstream stall (usable, but not live)."""
        q = {"symbol": symbol, "interval": interval, "set": set, "limit": limit}
        return _shaped(_call("GET", "/api/klines/indicators", query=q),
                       shaping.shape_indicators)

    @mcp.tool(annotations=_READONLY)
    def funding(
        symbol: str,
        history: bool = False,
        page: Annotated[int, Field(ge=1)] = 1,
    ) -> str:
        """Perp funding: current rate + mark/index + next funding time (positive
        rate = longs pay shorts). `history=true` lists past periods, newest first."""
        if history:
            q = {"symbol": symbol, "page": page, "page_size": 30}
            return _shaped(_call("GET", "/api/funding/history", query=q),
                           shaping.shape_funding_history)
        return _shaped(_call("GET", "/api/funding", query={"symbol": symbol}),
                       shaping.shape_funding)

    @mcp.tool(annotations=_READONLY)
    def indices(key: IndexKey | None = None) -> str:
        """External risk-weather panel: crypto Fear&Greed + BTC dominance, VIX,
        DXY, SPX, NDX, US10Y, gold. Omit `key` for the full snapshot. `⚠ stale`
        means the feed is down and this is the last good value."""
        if key:
            return _shaped(_call("GET", f"/api/indices/{key}"), shaping.shape_index)
        return _shaped(_call("GET", "/api/indices"), shaping.shape_indices)

    # ── account (testnet) ─────────────────────────────────────────────────────

    @mcp.tool(annotations=_READONLY)
    def positions() -> str:
        """Open testnet positions, one line each: side/qty/entry/mark/ROI%/leverage/
        margin mode/liquidation distance/TP-SL protection verdict/memo.
        SL✗(naked) or SL△(partial) means the position is not fully protected."""
        r = _call("GET", "/api/account/positions", query={"page_size": 50})
        return _shaped(r, shaping.shape_positions)

    @mcp.tool(annotations=_READONLY)
    def balance() -> str:
        """Account margin balance: equity / wallet / free / used / unrealized PnL
        (USDT, testnet)."""
        return _shaped(_call("GET", "/api/account/balance"), shaping.shape_balance)

    @mcp.tool(annotations=_READONLY)
    def pnl_drawdown() -> str:
        """Account risk snapshot in one call: equity + unrealized + total notional
        + exposure% + a brief per-position breakdown, plus drawdown vs the high-water
        mark (low `samples` = short snapshot history, low confidence). For protection
        legs / memos per position use `positions`."""
        pnl_r = _call("GET", "/api/account/pnl")
        dd_r = _call("GET", "/api/account/drawdown")
        pnl_ok = not errors.is_error(pnl_r)
        dd_ok = not errors.is_error(dd_r)
        return shaping.shape_pnl_drawdown(
            pnl_r.json if pnl_ok else None,
            dd_r.json if dd_ok else None,
            pnl_error=None if pnl_ok else errors.upstream_error_text(pnl_r),
            dd_error=None if dd_ok else errors.upstream_error_text(dd_r),
        )

    @mcp.tool(annotations=_READONLY)
    def open_orders(
        symbol: str | None = None,
        page: Annotated[int, Field(ge=1)] = 1,
    ) -> str:
        """Open (resting) orders incl. untriggered TP/SL legs, newest first:
        id/side/type/price-or-trigger/qty/status/flags [TP SL algo RO]/agent."""
        q = {"symbol": symbol, "page": page, "page_size": 30}
        return _shaped(_call("GET", "/api/account/orders/open", query=q),
                       shaping.shape_orders)

    @mcp.tool(annotations=_READONLY)
    def order_history(
        symbol: str,
        page: Annotated[int, Field(ge=1)] = 1,
        agent: str | None = None,
    ) -> str:
        """Order history for one symbol (filled/cancelled/conditional incl. algo
        legs), newest first. `agent` filters to one operator's orders."""
        q = {"symbol": symbol, "page": page, "page_size": 30, "agent": agent}
        return _shaped(_call("GET", "/api/account/orders", query=q),
                       shaping.shape_orders)

    @mcp.tool(annotations=_READONLY)
    def trades(
        symbol: str,
        page: Annotated[int, Field(ge=1)] = 1,
        agent: str | None = None,
    ) -> str:
        """Fill history for one symbol with realized PnL per fill + a page total,
        newest first. `agent` filters to one operator's fills."""
        q = {"symbol": symbol, "page": page, "page_size": 50, "agent": agent}
        return _shaped(_call("GET", "/api/account/trades", query=q),
                       shaping.shape_trades)

    @mcp.tool(annotations=_READONLY)
    def protection_status(symbol: str) -> str:
        """One symbol's TP/SL protection at a glance: the position, the primary
        leg of each kind (id/trigger/status), ladder counts, and whether the SL
        quantity covers the position. ORPHAN LEGS in the output = triggers with
        no position behind them — cancel them."""
        return _shaped(_call("GET", "/api/perp/protection", query={"symbol": symbol}),
                       shaping.shape_protection_status)

    # ── resources ─────────────────────────────────────────────────────────────

    @mcp.resource("sunday://manual", mime_type="text/markdown")
    def manual() -> str:
        """The engine's full agent-facing API manual (GET /manual) — the complete
        HTTP contract, including the long-tail endpoints that have no typed tool
        (journal / memory / reports) and the http_request fallback channel."""
        r = _call("GET", "/manual")
        if errors.is_error(r):
            raise ToolError(errors.upstream_error_text(r))
        return r.text

    # ── ops ───────────────────────────────────────────────────────────────────

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        # Always 200; the body tells the truth (sidecar up, engine maybe not).
        return JSONResponse({"ok": True, "version": __version__,
                             "engine": client.probe_health()})

    return mcp


def main() -> None:
    build_server().run(transport="streamable-http")
