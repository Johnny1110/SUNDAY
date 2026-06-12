#!/usr/bin/env bash
# Smoke the sunday-mcp sidecar end to end (PRD-9.1; extended each phase).
# Needs: a LIVE engine (:7777) with testnet keys, a LIVE sidecar (:7780), and
# the SDK installed (pip install -e 'engine[mcp]').
#   ./scripts/smoke-mcp.sh
# Phase 3 places REAL TESTNET orders (smallest viable size) on SMOKE_SYMBOL
# (default BTCUSDT) and closes everything it opens; the symbol must start flat.
set -eu
BASE="${SUNDAY_MCP_URL:-http://127.0.0.1:7780}"

echo "→ healthz"
HZ=$(curl -fsS "$BASE/healthz")
echo "$HZ" | grep -q '"ok": *true' || { echo "FAIL: healthz not ok: $HZ"; exit 1; }
echo "$HZ"

echo "→ MCP session (initialize / tools / resource / read tools / trade chain)"
SUNDAY_MCP_URL="$BASE" python3 - <<'EOF'
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl

# Phase 3 set: ping + 13 read-only (PRD-9.2) + 8 writes (PRD-9.3). Asserted as
# an exact set so an accidental extra tool fails the smoke too.
EXPECT = {
    "ping",
    "markets_list", "market_get", "klines", "indicators", "funding", "indices",
    "positions", "balance", "pnl_drawdown", "open_orders", "order_history",
    "trades", "protection_status",
    "place_order", "close_position", "set_protection", "cancel_order",
    "cancel_all_orders", "set_leverage_margin", "alert_set", "alerts_manage",
}

BUDGET = 60_000  # S3: design ceiling per tool result (evva truncates at 100k)

# Read tools at their worst LEGAL inputs (the live counterpart of the unit
# budget tests). Account-group tools pass on the empty-data path when flat.
READ_CALLS = [
    ("ping", {}),
    ("markets_list", {"page_size": 20}),
    ("market_get", {"symbol": "BTCUSDT"}),
    ("klines", {"symbol": "BTCUSDT", "interval": "1h", "limit": 500}),
    ("indicators", {"symbol": "BTCUSDT", "interval": "1h",
                    "set": "rsi,ema,sma,macd,bollinger,adx,atr", "limit": 400}),
    ("funding", {"symbol": "BTCUSDT"}),
    ("funding", {"symbol": "BTCUSDT", "history": True}),
    ("indices", {}),
    ("indices", {"key": "fear-greed"}),
    ("positions", {}),
    ("balance", {}),
    ("pnl_drawdown", {}),
    ("open_orders", {}),
    ("order_history", {"symbol": "BTCUSDT"}),
    ("trades", {"symbol": "BTCUSDT"}),
    ("protection_status", {"symbol": "BTCUSDT"}),
]

AGENT = "smoke-mcp"
SYM = os.environ.get("SMOKE_SYMBOL", "BTCUSDT")


def text_of(r) -> str:
    return r.content[0].text


async def read_tools(s) -> None:
    for name, args in READ_CALLS:
        r = await s.call_tool(name, args)
        t = text_of(r)
        assert not r.isError, f"{name} errored: {t!r}"
        assert len(t) < BUDGET, f"{name}: {len(t)} chars ≥ {BUDGET}"
        # a normal-result 5xx passthrough means the engine side is sick
        assert "[sunday 5" not in t, f"{name} upstream 5xx: {t!r}"
        if name == "ping":
            assert "UNREACHABLE" not in t, f"engine down: {t!r}"
        # * = the second variant of a twice-called tool (history / one key)
        label = name + ("*" if (args.get("history") or args.get("key")) else "")
        print(f"  {label:<18} {len(t):>6} chars · " + t.splitlines()[0][:90])

    # worst-case combo really came back whole (acceptance: no truncation)
    r = await s.call_tool("klines", {"symbol": "BTCUSDT", "interval": "1h",
                                     "limit": 500})
    lines = text_of(r).splitlines()
    bars = sum(1 for l in lines if l.split(",")[0].isdigit())  # ts-first rows
    assert bars == 500, f"klines 500-bar pull came back short: {bars}"


async def write_ok(s, name: str, args: dict) -> str:
    r = await s.call_tool(name, {"agent": AGENT, **args})
    t = text_of(r)
    assert not r.isError, f"{name} errored: {t!r}"
    assert "[sunday" not in t, f"{name} upstream error: {t!r}"
    return t


async def trade_chain(s) -> None:
    """PRD-9.3 acceptance chain. Hard rule at the end: ZERO residue on SYM."""

    # 0) the symbol must start flat — this script owns its own mess only
    t = text_of(await s.call_tool("protection_status", {"symbol": SYM}))
    assert "flat — no position, no trigger legs" in t, (
        f"pre-chain: {SYM} is not clean on testnet ({t!r}) — close it first or "
        "run with SMOKE_SYMBOL=<flat symbol>")

    # 1) schema rejects a naked order BEFORE it reaches the engine (S5/order_log clean)
    r = await s.call_tool("place_order", {
        "agent": AGENT, "symbol": SYM, "side": "buy", "type": "market",
        "notional_usd": 150, "take_profit": 1, "memo": "naked probe"})
    assert r.isError and "stop_loss" in text_of(r), (
        f"schema let a naked order through: {text_of(r)!r}")
    print("  reject (schema, no stop_loss): ok")

    # 2) cross-field rules reject before the engine too
    r = await s.call_tool("place_order", {
        "agent": AGENT, "symbol": SYM, "side": "buy", "type": "market",
        "qty": 0.002, "notional_usd": 150, "take_profit": 70000,
        "stop_loss": 60000, "memo": "xor probe"})
    assert r.isError and "exactly one" in text_of(r), text_of(r)
    print("  reject (validate, qty xor notional):", text_of(r)[:72])

    t = text_of(await s.call_tool("open_orders", {"symbol": SYM}))
    assert "no orders" in t, f"a rejected order left residue: {t!r}"

    # 3) pre-entry config — both segments must report
    t = await write_ok(s, "set_leverage_margin",
                       {"symbol": SYM, "leverage": 3, "margin_mode": "isolated"})
    assert "margin_mode: isolated (" in t and "leverage: 3x set" in t, t
    print("  set_leverage_margin:", " · ".join(t.splitlines()))

    # 4) entry with both legs (sized by notional; triggers vs the live mark)
    mark = float(text_of(await s.call_tool("funding", {"symbol": SYM}))
                 .split("mark ")[1].split(" ")[0])
    tp, sl = round(mark * 1.05), round(mark * 0.95)
    t = await write_ok(s, "place_order", {
        "symbol": SYM, "side": "buy", "type": "market", "notional_usd": 150,
        "take_profit": tp, "stop_loss": sl,
        "memo": "smoke-mcp phase-3 chain (auto-closes itself)"})
    assert "placed:" in t and "TP leg #" in t and "SL leg #" in t, t
    print("  place_order:", t.splitlines()[0])
    await asyncio.sleep(2)  # let position/legs settle into the book reads

    # 5) the position is protected and the flags agree
    t = text_of(await s.call_tool("protection_status", {"symbol": SYM}))
    assert "TP #" in t and "SL #" in t and "covers qty: true" in t, t
    assert not t.startswith("ORPHAN"), t
    print("  protection_status:", t.splitlines()[0])
    t = text_of(await s.call_tool("positions", {}))
    assert SYM in t and "TP✓ SL✓" in t, f"positions flags disagree: {t!r}"

    # 6) move the stop (replace flow must report the old leg)
    t = await write_ok(s, "set_protection",
                       {"symbol": SYM, "stop_loss": round(mark * 0.96)})
    assert "replaced old legs: #" in t, f"no replaced ids: {t!r}"
    print("  set_protection:", next(l for l in t.splitlines() if "replaced" in l))
    await asyncio.sleep(1)

    # 7) flatten — the engine must sweep the orphaned legs and say which
    t = await write_ok(s, "close_position", {"symbol": SYM})
    assert "closed:" in t and "cancelled protection legs: #" in t, t
    print("  close_position:", next(l for l in t.splitlines() if "cancelled" in l))
    await asyncio.sleep(1)

    # 8) HARD assert: zero residue (the orphan-leg rule, PRD-9.3 §4)
    t = text_of(await s.call_tool("open_orders", {"symbol": SYM}))
    assert "no orders" in t, f"ORPHAN legs survived the close: {t!r}"
    t = text_of(await s.call_tool("protection_status", {"symbol": SYM}))
    assert "flat — no position, no trigger legs" in t, t
    print("  orphan check: clean")

    # 9) audit chain (S4): every write this script made is attributed to it
    t = text_of(await s.call_tool("order_history", {"symbol": SYM, "agent": AGENT}))
    rows = [l for l in t.splitlines() if l.startswith("#")]
    assert len(rows) >= 3, f"audit rows missing (got {len(rows)}):\n{t}"
    assert all(l.rstrip().endswith("· " + AGENT) for l in rows), (
        "audit attribution broken:\n" + t)
    print(f"  audit: {len(rows)} writes attributed to {AGENT}")

    # 10) cancel tools — deterministic probes (no state games):
    #     a bogus id exercises the -2011 hint path end to end…
    r = await s.call_tool("cancel_order", {"agent": AGENT, "symbol": SYM,
                                           "order_id": "999999999999999999"})
    t = text_of(r)
    # upstream error must come back as a NORMAL result (passthrough), not a crash
    assert not r.isError and t.startswith("[sunday"), f"passthrough broken: {t!r}"
    print("  cancel_order (bogus id):", t.splitlines()[0][:90])
    #     …and cancel-all on a flat book is a safe no-op that must still succeed
    t = await write_ok(s, "cancel_all_orders", {"symbol": SYM})
    assert "all resting orders cancelled" in t, t
    print("  cancel_all_orders (flat book): ok")

    # 11) alerts mini-chain — set, list, delete, gone
    t = await write_ok(s, "alert_set", {
        "symbol": SYM, "kind": "price_above", "threshold": round(mark * 3),
        "note": "smoke-mcp probe (deletes itself)"})
    assert t.startswith("#") and "fires once" in t, t
    alert_id = int(t.split()[0][1:])
    t = await write_ok(s, "alerts_manage", {"action": "list"})
    assert f"#{alert_id} " in t, t
    t = await write_ok(s, "alerts_manage", {"action": "delete", "id": alert_id})
    assert f"deleted alert #{alert_id}" in t, t
    t = await write_ok(s, "alerts_manage", {"action": "list", "status": "active"})
    assert f"#{alert_id} " not in t, t
    print(f"  alerts: set → list → delete #{alert_id} ok")


async def main() -> None:
    url = os.environ["SUNDAY_MCP_URL"] + "/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()

            tools = {t.name for t in (await s.list_tools()).tools}
            assert tools == EXPECT, (
                f"tool set drift: extra={sorted(tools - EXPECT)}"
                f" missing={sorted(EXPECT - tools)}")

            uris = {str(r.uri) for r in (await s.list_resources()).resources}
            assert "sunday://manual" in uris, f"manual resource missing: {uris}"
            manual = await s.read_resource(AnyUrl("sunday://manual"))
            text = manual.contents[0].text
            assert len(text) > 5_000 and "## " in text, (
                f"manual looks wrong: {len(text)} chars")
            print(f"  resource sunday://manual: {len(text)} chars")

            await read_tools(s)
            await trade_chain(s)

            print(f"smoke-mcp: OK — {len(tools)} tools, manual resource, "
                  f"{len(READ_CALLS)} read calls within budget, trade chain clean")


asyncio.run(main())
EOF
