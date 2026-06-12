"""FastMCP server assembly — the ONLY module that imports the `mcp` SDK (S7).

Tools are deliberately thin: input is already schema-validated by the SDK,
so a tool body is `client.call → shaping/errors pure functions → str`.
Read tools carry readOnlyHint annotations (no effect under the swarm's
bypass permission mode today; hygiene for any stricter future host).

Output budget (S3): page_size caps live HERE in the schemas (markets 20,
open/history orders 30, trades 50, funding history 30, klines 500 bars,
alerts 30) — that, plus one-line-per-row shaping, keeps every tool under the
60k-char design budget. Don't raise a cap without extending the budget tests.

Write tools (PRD-9.3): schema-required agent/TP/SL/memo make naked or
anonymous entries unrepresentable; cross-field rules run in validate.py
BEFORE any upstream call; a connection-layer failure on a write surfaces as
placed-or-not UNKNOWN and is never auto-retried (S5).
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

from . import __version__, client, errors, shaping, validate

_READONLY = ToolAnnotations(readOnlyHint=True)
_DESTRUCTIVE = ToolAnnotations(destructiveHint=True)

# Write tools (S4): the caller's self-reported identity, recorded in the audit
# ledger via the X-Agent header — schema-required so anonymous writes can't
# happen through this channel at all.
AgentParam = Annotated[str, Field(min_length=1, max_length=32,
                                  description="your agent id, recorded in the audit ledger")]

Interval = Literal["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h",
                   "12h", "1d", "3d", "1w", "1M"]
IndexKey = Literal["fear-greed", "btc-dominance", "vix", "dxy", "spx", "ndx",
                   "us10y", "gold", "oil"]


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


def _write(method: str, path: str, agent: str, shaper, *,
           body: dict | None = None, query: dict | None = None) -> str:
    """One engine WRITE: X-Agent attribution (S4), zero retry (client retries
    GETs only, S5), and the unreachable case mapped to the placed-or-not-UNKNOWN
    error — a timed-out write may have landed, so a blind resend is forbidden."""
    try:
        reply = client.call(method, path, body=body, query=query, agent=agent)
    except client.EngineUnreachable as e:
        raise ToolError(errors.WRITE_UNREACHABLE_TEXT) from e
    return _shaped(reply, shaper)


def _reject(violations: list[str]) -> None:
    """Cross-field validation verdict (validate.py) — raised BEFORE any upstream
    call, so a rejected input never reaches Sunday (order_log stays clean)."""
    if violations:
        raise ToolError("rejected before reaching the engine: " + "; ".join(violations))


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
        Full API contract: resource sunday://manual (or http GET /manual)."""
        q: dict = {"sort": sort, "order": order, "page": page, "page_size": page_size}
        if search:
            q["symbol"] = search
        return _shaped(_call("GET", "/api/markets", query=q), shaping.shape_markets)

    @mcp.tool(annotations=_READONLY)
    def market_get(symbol: str) -> str:
        """One market: live ticker + the numbers every order must respect —
        price/qty precision, min/max qty, min notional, max leverage, fees."""
        return _shaped(_call("GET", f"/api/markets/{validate.norm_symbol(symbol)}"),
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
        q = {"symbol": validate.norm_symbol(symbol), "interval": interval,
             "limit": limit, "start": start, "end": end}
        return _shaped(_call("GET", "/api/klines", query=q), shaping.shape_klines)

    @mcp.tool(annotations=_READONLY)
    def indicators(
        symbol: str,
        interval: Interval = "1h",
        set: str = "rsi,ema,sma,macd,bollinger,adx,atr",
        limit: Annotated[int, Field(ge=200, le=400)] = 200,
    ) -> str:
        """Current technical-indicator panel (latest values) over recent candles:
        rsi, ema(20/50), sma(20/50), macd, bollinger(+z), adx, atr — `set` is a
        comma list. A `⚠ stale` first line means the engine served last-good
        data after an upstream stall (usable, but not live)."""
        q = {"symbol": validate.norm_symbol(symbol), "interval": interval,
             "set": set, "limit": limit}
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
        sym = validate.norm_symbol(symbol)
        if history:
            q = {"symbol": sym, "page": page, "page_size": 30}
            return _shaped(_call("GET", "/api/funding/history", query=q),
                           shaping.shape_funding_history)
        return _shaped(_call("GET", "/api/funding", query={"symbol": sym}),
                       shaping.shape_funding)

    @mcp.tool(annotations=_READONLY)
    def indices(key: IndexKey | None = None) -> str:
        """External risk-weather panel: crypto Fear&Greed + BTC dominance, VIX,
        DXY, SPX, NDX, US10Y, gold, Brent oil. Omit `key` for the full snapshot.
        `⚠ stale` means the feed is down and this is the last good value."""
        if key:
            return _shaped(_call("GET", f"/api/indices/{key}"), shaping.shape_index)
        return _shaped(_call("GET", "/api/indices"), shaping.shape_indices)

    # ── account (testnet) ─────────────────────────────────────────────────────

    @mcp.tool(annotations=_READONLY)
    def positions(page: Annotated[int, Field(ge=1)] = 1) -> str:
        """Open testnet positions, one line each: side/qty/entry/mark/ROI%/leverage/
        margin mode/liquidation distance/TP-SL protection verdict/memo.
        SL✗(naked) or SL△(partial) means the position is not fully protected."""
        r = _call("GET", "/api/account/positions",
                  query={"page": page, "page_size": 50})
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
        q = {"symbol": validate.norm_symbol(symbol) if symbol else None,
             "page": page, "page_size": 30}
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
        q = {"symbol": validate.norm_symbol(symbol), "page": page,
             "page_size": 30, "agent": agent}
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
        q = {"symbol": validate.norm_symbol(symbol), "page": page,
             "page_size": 50, "agent": agent}
        return _shaped(_call("GET", "/api/account/trades", query=q),
                       shaping.shape_trades)

    @mcp.tool(annotations=_READONLY)
    def protection_status(symbol: str) -> str:
        """One symbol's TP/SL protection at a glance: the position, the primary
        leg of each kind (id/trigger/status), ladder counts, and whether the SL
        quantity covers the position. ORPHAN LEGS in the output = triggers with
        no position behind them — cancel them."""
        return _shaped(_call("GET", "/api/perp/protection",
                             query={"symbol": validate.norm_symbol(symbol)}),
                       shaping.shape_protection_status)

    # ── trading writes (testnet; PRD-9.3 — schema is the law) ─────────────────

    @mcp.tool()
    def place_order(
        agent: AgentParam,
        symbol: str,
        side: Literal["buy", "sell"],
        type: Literal["market", "limit"],
        take_profit: Annotated[float, Field(gt=0, description="TP trigger price — required: no naked entries")],
        stop_loss: Annotated[float, Field(gt=0, description="SL trigger price — required: no naked entries")],
        memo: Annotated[str, Field(min_length=1, max_length=300,
                                   description="why this trade — shown to the User on the position")],
        qty: Annotated[float | None, Field(gt=0, description="size in contracts (XOR notional_usd)")] = None,
        notional_usd: Annotated[float | None, Field(gt=0, description="size in USDT (XOR qty)")] = None,
        price: Annotated[float | None, Field(gt=0, description="limit price (type=limit only)")] = None,
        leverage: Annotated[int | None, Field(ge=1, le=125)] = None,
        margin_mode: Literal["isolated", "cross"] | None = None,
    ) -> str:
        """The ONLY entry path: open a perp position (testnet) with TP/SL legs
        attached in the same call. Size with exactly one of qty / notional_usd.
        Trigger prices are validated by the engine against the TESTNET mark —
        a trigger already in its fire zone is refused (it would market-close
        instantly). Placing is not done: verify via protection_status next."""
        _reject(validate.order_violations(side=side, type=type, qty=qty,
                                          notional_usd=notional_usd, price=price,
                                          take_profit=take_profit, stop_loss=stop_loss))
        body = {"symbol": validate.norm_symbol(symbol), "side": side, "type": type,
                "take_profit": take_profit, "stop_loss": stop_loss, "memo": memo,
                "qty": qty, "notional_usd": notional_usd, "price": price,
                "leverage": leverage, "margin_mode": margin_mode}
        return _write("POST", "/api/perp/order", agent, shaping.shape_order_result,
                      body={k: v for k, v in body.items() if v is not None})

    @mcp.tool(annotations=_DESTRUCTIVE)
    def close_position(agent: AgentParam, symbol: str) -> str:
        """Flatten the symbol's open position with a reduce-only market order;
        the engine then sweeps the now-orphaned TP/SL trigger legs and reports
        them as `cancelled protection legs`."""
        return _write("POST", "/api/perp/close", agent, shaping.shape_close_result,
                      body={"symbol": validate.norm_symbol(symbol)})

    @mcp.tool()
    def set_protection(
        agent: AgentParam,
        symbol: str,
        take_profit: Annotated[float | None, Field(gt=0, description="new TP trigger; null keeps current TP legs")] = None,
        stop_loss: Annotated[float | None, Field(gt=0, description="new SL trigger; null keeps current SL legs")] = None,
    ) -> str:
        """Attach or REPLACE the TP/SL legs of an existing position (move a
        stop, re-protect after a partial close) without re-opening it. The
        engine places each new full-size reduce-only leg FIRST and only then
        cancels the old legs of that kind — the position is never naked
        mid-swap. Give at least one trigger."""
        _reject(validate.protection_violations(take_profit, stop_loss))
        body = {"symbol": validate.norm_symbol(symbol),
                "take_profit": take_profit, "stop_loss": stop_loss}
        return _write("POST", "/api/perp/protection", agent,
                      shaping.shape_protection_result,
                      body={k: v for k, v in body.items() if v is not None})

    @mcp.tool(annotations=_DESTRUCTIVE)
    def cancel_order(agent: AgentParam, symbol: str, order_id: str) -> str:
        """Cancel ONE resting order by id — works for both books (regular and
        algo/conditional: the engine retries the algo book on -2011)."""
        return _write("DELETE", f"/api/perp/order/{order_id}", agent,
                      shaping.shape_cancel_result,
                      query={"symbol": validate.norm_symbol(symbol)})

    @mcp.tool(annotations=_DESTRUCTIVE)
    def cancel_all_orders(agent: AgentParam, symbol: str) -> str:
        """Cancel EVERY resting order on the symbol — including its TP/SL
        protection legs: a remaining position is naked afterwards (re-attach
        via set_protection). For one order use cancel_order instead."""
        return _write("DELETE", "/api/perp/orders", agent,
                      shaping.shape_cancel_all_result,
                      query={"symbol": validate.norm_symbol(symbol)})

    @mcp.tool()
    def set_leverage_margin(
        agent: AgentParam,
        symbol: str,
        leverage: Annotated[int | None, Field(ge=1, le=125)] = None,
        margin_mode: Literal["isolated", "cross"] | None = None,
    ) -> str:
        """Configure the symbol before an entry: margin mode and/or leverage
        (margin mode is applied first). Margin mode can't change while a
        position or open orders exist (the engine answers 409). Each segment
        reports its own outcome — a half-success is shown as such."""
        _reject(validate.leverage_margin_violations(leverage, margin_mode))
        margin = lev = None
        margin_err = lev_err = None
        symbol = validate.norm_symbol(symbol)
        if margin_mode is not None:
            try:
                r = client.call("POST", "/api/perp/margin-mode", agent=agent,
                                body={"symbol": symbol, "mode": margin_mode})
                margin = r.json if not errors.is_error(r) else None
                margin_err = None if margin else errors.upstream_error_text(r)
            except client.EngineUnreachable:
                margin_err = errors.WRITE_UNREACHABLE_TEXT
        if leverage is not None:
            if margin_err == errors.WRITE_UNREACHABLE_TEXT:
                lev_err = "skipped (engine unreachable)"
            else:
                try:
                    r = client.call("POST", "/api/perp/leverage", agent=agent,
                                    body={"symbol": symbol, "leverage": leverage})
                    lev = r.json if not errors.is_error(r) else None
                    lev_err = None if lev else errors.upstream_error_text(r)
                except client.EngineUnreachable:
                    lev_err = errors.WRITE_UNREACHABLE_TEXT
        return shaping.shape_leverage_margin(margin, lev, margin_err, lev_err)

    @mcp.tool()
    def alert_set(
        agent: AgentParam,
        symbol: str,
        kind: Literal["price_above", "price_below", "pct_move"],
        threshold: Annotated[float, Field(gt=0, description="price level, or |%| for pct_move")],
        note: Annotated[str | None, Field(max_length=120)] = None,
    ) -> str:
        """Set a price alert (mainnet prices). pct_move captures the current
        price as its reference at creation. An alert fires ONCE (webhook +
        Telegram), then flips to status=triggered."""
        body = {"symbol": validate.norm_symbol(symbol), "kind": kind,
                "threshold": threshold, "note": note}
        return _write("POST", "/api/alerts", agent, shaping.shape_alert_created,
                      body={k: v for k, v in body.items() if v is not None})

    @mcp.tool()
    def alerts_manage(
        agent: AgentParam,
        action: Literal["list", "delete"],
        id: Annotated[int | None, Field(description="alert id (required for delete)")] = None,
        status: Annotated[Literal["active", "triggered"] | None,
                          Field(description="list filter")] = None,
    ) -> str:
        """List alerts (newest first, one line each) or delete one by id."""
        _reject(validate.alerts_violations(action, id))
        if action == "delete":
            return _write("DELETE", f"/api/alerts/{id}", agent,
                          shaping.shape_alert_deleted)
        q = {"status": status, "page_size": 30}
        return _shaped(_call("GET", "/api/alerts", query=q), shaping.shape_alerts_list)

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
