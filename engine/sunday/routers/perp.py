"""/api/perp — perpetual order flow on the testnet account (req 1).

The agent places orders here like a human would on Binance: market/limit entries
sized by contracts or USD notional, with optional leverage, margin mode (isolated/
cross 逐倉/全倉), and attached take-profit / stop-loss. TP/SL are placed as reduce-only
TAKE_PROFIT_MARKET / STOP_MARKET legs after the entry — the same primitive the
exchange uses for native brackets. Since Binance's Algo-Service migration those legs
live in a separate conditional book (id = algoId); /api/perp/protection manages them
for an existing position without re-opening it (PRD-003).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .. import exchange, protection as riskmath, store
from ..apiutil import agent_of, ex_call, require_trade_key, to_float

router = APIRouter(prefix="/api/perp", tags=["perp"])
log = logging.getLogger("sunday.perp")

# Every order-book write takes the caller's self-reported identity from this header
# and records it in the audit log (BUG-03) — anonymous writes still work but show
# agent: null in the account listings, i.e. unattributable on purpose.
_X_AGENT = Header(default=None, alias="X-Agent",
                  description="caller's agent id, recorded in the audit log")


def _audit(symbol: str, order_id, agent: str | None, action: str,
           memo: str | None = None, order: dict | None = None) -> None:
    """Best-effort audit write (BUG-03): the exchange side effect already happened, so
    a storage hiccup must surface in logs — never as a 5xx that makes the agent retry
    an order that actually filled."""
    try:
        store.record_order(symbol.upper(), order_id, memo, order or {}, agent=agent, action=action)
    except Exception as e:
        log.warning("audit log write failed (%s %s): %s", action, symbol, e)


class OrderReq(BaseModel):
    symbol: str
    side: str                              # buy | sell
    type: str = "market"                   # market | limit
    qty: float | None = None               # contracts; or use notional_usd
    notional_usd: float | None = None      # sized to qty via current price
    price: float | None = None             # required for limit
    leverage: int | None = None            # set before the entry if given
    margin_mode: str | None = None         # isolated | cross
    reduce_only: bool = False
    take_profit: float | None = None       # trigger price → reduce-only TP leg
    stop_loss: float | None = None         # trigger price → reduce-only SL leg
    memo: str | None = Field(default=None, max_length=300)  # agent's rationale; shown to the User


class LeverageReq(BaseModel):
    symbol: str
    leverage: int


class MarginModeReq(BaseModel):
    symbol: str
    mode: str                              # isolated | cross


class CloseReq(BaseModel):
    symbol: str


class ProtectionReq(BaseModel):
    """Attach/replace TP/SL trigger legs on an EXISTING position (no new entry).
    A null price leaves that leg kind untouched."""
    symbol: str
    take_profit: float | None = None       # trigger price; null = keep current TP legs
    stop_loss: float | None = None         # trigger price; null = keep current SL legs


def _norm_order(o: dict) -> dict:
    return {
        "id": o.get("id"), "symbol": o.get("symbol"), "type": o.get("type"),
        "side": o.get("side"), "status": o.get("status"),
        "price": to_float(o.get("price")), "amount": to_float(o.get("amount")),
        "filled": to_float(o.get("filled")), "reduce_only": o.get("reduceOnly"),
        "trigger_price": to_float(o.get("triggerPrice")), "ts": o.get("timestamp"),
        # conditional legs live in Binance's Algo Service: this id is an algoId
        "algo": bool((o.get("info") or {}).get("algoId")),
    }


def _set_margin_mode_safe(symbol: str, mode: str) -> str:
    """Set margin mode, tolerating Binance's two benign rejections and reporting which:
      'set'       — changed;
      'unchanged' — already that mode (-4046 'No need to change margin type');
      'blocked'   — can't change while a position / open orders exist (-4047).
    Only genuinely unexpected errors raise. Callers decide whether 'blocked' is fatal."""
    try:
        exchange.set_margin_mode(symbol, mode)
        return "set"
    except Exception as e:
        s = str(e).lower()
        if "-4046" in s or "no need to change" in s:
            return "unchanged"
        if "-4047" in s or "cannot be changed if there exists" in s:
            return "blocked"
        raise HTTPException(502, f"set_margin_mode failed: {type(e).__name__}: {str(e)[:200]}")


def _reject_immediate(kind: str, trigger: float, mark: float, close_side: str) -> None:
    """400 for a trigger already inside its fire zone. Binance's Algo Service does NOT
    reject such a leg (-2021 was the legacy book) — it executes it immediately as a
    reduce-only market close (BUG-01/BUG-04), so Sunday must refuse it up front."""
    pos_side = "long" if close_side == "sell" else "short"
    want = {("stop_loss", "long"): "BELOW", ("take_profit", "long"): "ABOVE",
            ("stop_loss", "short"): "ABOVE", ("take_profit", "short"): "BELOW"}[(kind, pos_side)]
    raise HTTPException(400,
        f"{kind} {trigger} would trigger immediately and market-close the position the "
        f"moment it lands: trigger legs are judged against the TESTNET mark price "
        f"(currently {mark}) and this price is already in the fire zone. For a {pos_side}, "
        f"{kind} must sit {want} the current testnet mark. Adjust the trigger and re-place; "
        f"for an unfilled limit entry, attach TP/SL after the fill via POST /api/perp/protection.")


def _validate_triggers(close_side: str, mark: float | None,
                       take_profit: float | None, stop_loss: float | None) -> None:
    """Refuse trigger prices that would fire instantly vs the testnet mark — checked
    BEFORE any exchange write so a bad request has zero side effects. mark None
    (testnet feed hiccup) degrades to no check: workingType=MARK_PRICE still applies."""
    for kind, trig in (("take_profit", take_profit), ("stop_loss", stop_loss)):
        if trig and riskmath.immediate_trigger(close_side, kind == "take_profit", trig, mark):
            _reject_immediate(kind, trig, mark, close_side)


def _resolve_qty(req: OrderReq) -> float:
    if req.qty and req.qty > 0:
        return ex_call(lambda: exchange.amount_to_precision(req.symbol, req.qty))
    if req.notional_usd and req.notional_usd > 0:
        price = req.price if (req.type == "limit" and req.price) else \
            ex_call(lambda: to_float(exchange.fetch_ticker(req.symbol).get("last")))
        if not price:
            raise HTTPException(502, "could not resolve a price to size notional_usd")
        return ex_call(lambda: exchange.amount_to_precision(req.symbol, req.notional_usd / price))
    raise HTTPException(400, "provide qty (contracts) or notional_usd")


def _place_entry(req: "OrderReq", qty: float, params: dict) -> dict:
    """Place the entry order; turn Binance's PERCENT_PRICE rejection (-4016/-4017) into a
    clear 400 explaining the limit must sit within the symbol's % band of the mark."""
    try:
        return exchange.create_order(req.symbol, req.type, req.side, qty, req.price, params)
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if any(t in msg for t in ("-4016", "-4017", "PERCENT_PRICE", "Limit price can")):
            raise HTTPException(400,
                f"limit price rejected by Binance's PERCENT_PRICE filter: {msg[:140]}. "
                "A limit price must stay within the symbol's allowed % band around the mark — "
                "move it closer to the current price, or use type=market.")
        raise HTTPException(502, f"exchange error: {type(e).__name__}: {msg[:300]}")


@router.post("/order")
def place_order(req: OrderReq, x_agent: str | None = _X_AGENT) -> dict:
    """Place a perp order. side=buy|sell, type=market|limit; size by qty or notional_usd;
    optional leverage / margin_mode / take_profit / stop_loss."""
    require_trade_key()
    agent = agent_of(x_agent)
    if req.side not in ("buy", "sell"):
        raise HTTPException(400, "side must be 'buy' or 'sell'")
    if req.type not in ("market", "limit"):
        raise HTTPException(400, "type must be 'market' or 'limit'")
    if req.type == "limit" and not req.price:
        raise HTTPException(400, "limit orders require a price")
    if req.take_profit or req.stop_loss:
        close_side = "sell" if req.side == "buy" else "buy"
        _validate_triggers(close_side, exchange.fetch_mark_price(req.symbol),
                           req.take_profit, req.stop_loss)

    applied: dict = {}
    if req.margin_mode:
        if req.margin_mode not in ("isolated", "cross"):
            raise HTTPException(400, "margin_mode must be 'isolated' or 'cross'")
        result = _set_margin_mode_safe(req.symbol, req.margin_mode)
        applied["margin_mode"] = req.margin_mode
        if result == "blocked":  # a position/open orders exist → keep current mode, still place the order
            applied["margin_mode_note"] = "unchanged: a position or open orders already exist on this symbol (Binance -4047)"
    if req.leverage:
        ex_call(lambda: exchange.set_leverage(req.symbol, req.leverage))
        applied["leverage"] = req.leverage

    qty = _resolve_qty(req)
    params = {"reduceOnly": True} if req.reduce_only else {}
    entry = _place_entry(req, qty, params)

    # Attach reduce-only TP/SL legs (only meaningful for an opening order).
    legs: dict = {}
    if not req.reduce_only and (req.take_profit or req.stop_loss):
        close_side = "sell" if req.side == "buy" else "buy"
        for kind, trig, is_tp in (("take_profit", req.take_profit, True),
                                  ("stop_loss", req.stop_loss, False)):
            if not trig:
                continue
            legs[kind] = _norm_order(ex_call(lambda: exchange.place_stop(
                req.symbol, close_side, qty, trig, take_profit=is_tp)))
            _audit(req.symbol, legs[kind]["id"], agent, "protection", order={
                "side": close_side, "type": legs[kind]["type"], "qty": qty,
                "reduce_only": True, kind: trig})

    # Journal the decision: params (one column each) + the agent's memo. Joined into
    # /api/account/positions so the User sees WHY this position was opened.
    _audit(req.symbol, entry.get("id"), agent, "order", req.memo, {
        "side": req.side, "type": req.type, "qty": qty,
        "notional_usd": req.notional_usd, "price": req.price,
        "leverage": req.leverage, "margin_mode": req.margin_mode, "reduce_only": req.reduce_only,
        "take_profit": req.take_profit, "stop_loss": req.stop_loss,
    })

    return {"ok": True, "applied": applied, "order": _norm_order(entry), "memo": req.memo, **legs}


@router.post("/leverage")
def set_leverage(req: LeverageReq) -> dict:
    require_trade_key()
    if req.leverage < 1:
        raise HTTPException(400, "leverage must be ≥ 1")
    ex_call(lambda: exchange.set_leverage(req.symbol, req.leverage))
    return {"ok": True, "symbol": req.symbol.upper(), "leverage": req.leverage}


@router.post("/margin-mode")
def set_margin_mode(req: MarginModeReq) -> dict:
    require_trade_key()
    if req.mode not in ("isolated", "cross"):
        raise HTTPException(400, "mode must be 'isolated' or 'cross'")
    result = _set_margin_mode_safe(req.symbol, req.mode)
    if result == "blocked":
        raise HTTPException(409, f"cannot change margin mode for {req.symbol.upper()} while a position or open orders exist")
    return {"ok": True, "symbol": req.symbol.upper(), "margin_mode": req.mode, "result": result}


@router.post("/close")
def close(req: CloseReq, x_agent: str | None = _X_AGENT) -> dict:
    """Flatten an open position with a reduce-only market order, then cancel the
    symbol's now-orphaned TP/SL trigger legs (BUG-02 — Binance strands them
    inconsistently). ``cancelled_protection`` lists the swept leg ids."""
    require_trade_key()
    result = ex_call(lambda: exchange.close_position(req.symbol))
    if result is None:
        raise HTTPException(404, f"no open position for {req.symbol.upper()}")
    closed = _norm_order(result)
    _audit(req.symbol, closed.get("id"), agent_of(x_agent), "close", order={
        "side": closed.get("side"), "type": closed.get("type"),
        "qty": closed.get("amount"), "reduce_only": True})
    out = {"ok": True, "closed": closed}
    try:
        cancelled, failed = exchange.sweep_orphan_legs(req.symbol)
        out["cancelled_protection"] = cancelled
        if failed:
            out["cancel_failed"] = failed
    except Exception as e:  # the flatten DID happen — report the sweep miss, don't 5xx
        out["protection_sweep_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


@router.delete("/order/{order_id}")
def cancel_order(order_id: str, symbol: str, x_agent: str | None = _X_AGENT) -> dict:
    """Cancel one resting order (symbol required by Binance fapi)."""
    require_trade_key()
    ex_call(lambda: exchange.cancel_order(order_id, symbol))
    _audit(symbol, order_id, agent_of(x_agent), "cancel")
    return {"ok": True, "cancelled": order_id}


@router.delete("/orders")
def cancel_all(symbol: str, x_agent: str | None = _X_AGENT) -> dict:
    """Cancel all resting orders for a symbol."""
    require_trade_key()
    ex_call(lambda: exchange.cancel_all_orders(symbol))
    _audit(symbol, None, agent_of(x_agent), "cancel")
    return {"ok": True, "symbol": symbol.upper()}


# --------------------------------------------------------------------------
# TP/SL protection legs on an existing position (PRD-003 §2b)
# --------------------------------------------------------------------------

def _position_for(symbol: str) -> dict | None:
    """The raw positionRisk row for `symbol`, or None when flat."""
    sym = symbol.upper()
    for p in exchange.fetch_positions():
        if p.get("symbol") == sym and to_float(p.get("positionAmt")):
            return p
    return None


def _trigger_legs(symbol: str) -> list[dict]:
    """Open TP/SL trigger legs for one symbol, with the detail the protection view and
    the replace flow need (id / trigger_price / amount / algo-book membership)."""
    out: list[dict] = []
    for o in exchange.fetch_open_orders(symbol):
        leg = riskmath.classify_leg(o.get("type"))
        if leg:
            out.append({
                "tp_sl": leg, "id": str(o.get("orderId")),
                "trigger_price": to_float(o.get("stopPrice")),
                "status": (o.get("status") or "").lower(),
                "amount": to_float(o.get("origQty")),
                "close_position": bool(o.get("closePosition")),
                "side": (o.get("side") or "").lower(),
                "algo": bool(o.get("algo")), "ts": o.get("time"),
            })
    return out


def _place_protection_leg(symbol: str, close_side: str, qty: float,
                          trigger: float, take_profit: bool) -> dict:
    """Place one reduce-only trigger leg; turn Binance's immediate-trigger rejection
    into a 400 that tells the agent which side of the mark the trigger must sit on."""
    try:
        return exchange.place_stop(symbol, close_side, qty, trigger, take_profit=take_profit)
    except Exception as e:
        msg = str(e)
        if "-2021" in msg or "immediately trigger" in msg.lower():
            raise HTTPException(400,
                f"trigger price {trigger} would fire immediately: for a long, stop_loss must sit "
                "BELOW the mark and take_profit ABOVE it (mirrored for a short). Check the mark "
                f"via GET /api/funding/{symbol.upper()} and re-place. Binance said: {msg[:140]}")
        raise HTTPException(502, f"exchange error: {type(e).__name__}: {msg[:300]}")


@router.get("/protection")
def get_protection(symbol: str) -> dict:
    """A symbol's TP/SL protection at a glance: the primary leg of each kind (newest),
    ladder counts, SL coverage, and the position it protects (null when flat — any legs
    listed then are orphans worth cancelling)."""
    require_trade_key()
    p = ex_call(lambda: _position_for(symbol))
    legs = ex_call(lambda: _trigger_legs(symbol))
    amt = to_float(p.get("positionAmt")) if p else None
    position = None
    if p and amt:
        position = {
            "side": "long" if amt > 0 else "short", "qty": abs(amt),
            "entry": to_float(p.get("entryPrice")), "mark": to_float(p.get("markPrice")),
            "leverage": int(to_float(p.get("leverage")) or 0) or None,
        }
    return {"symbol": symbol.upper(), "position": position,
            **riskmath.protection_detail(abs(amt) if amt else 0.0, legs)}


@router.post("/protection")
def set_protection(req: ProtectionReq, x_agent: str | None = _X_AGENT) -> dict:
    """Attach or replace the TP/SL legs of an EXISTING position without re-opening it
    (after a partial close, a dropped leg, or to move a stop). For each price given, a
    new full-size reduce-only leg is placed FIRST and the old legs of that kind are
    cancelled after — the position is never left naked mid-swap. Null leaves a kind as-is."""
    require_trade_key()
    agent = agent_of(x_agent)
    if req.take_profit is None and req.stop_loss is None:
        raise HTTPException(400, "provide take_profit and/or stop_loss (trigger price)")
    if (req.take_profit is not None and req.take_profit <= 0) or \
       (req.stop_loss is not None and req.stop_loss <= 0):
        raise HTTPException(400, "trigger prices must be > 0")

    p = ex_call(lambda: _position_for(req.symbol))
    if not p:
        raise HTTPException(404,
            f"no open position for {req.symbol.upper()} — protection legs attach to an "
            "existing position; open one via POST /api/perp/order")
    amt = to_float(p.get("positionAmt")) or 0.0
    qty = abs(amt)
    close_side = "sell" if amt > 0 else "buy"
    # Both triggers vetted against the position's (testnet) mark BEFORE any leg is
    # placed — a bad stop_loss must not leave a fresh take_profit half-applied.
    _validate_triggers(close_side, to_float(p.get("markPrice")), req.take_profit, req.stop_loss)
    old = ex_call(lambda: _trigger_legs(req.symbol))

    out: dict = {"ok": True, "symbol": req.symbol.upper()}
    replaced: list[str] = []
    cancel_failed: list[str] = []
    for kind, price in (("take_profit", req.take_profit), ("stop_loss", req.stop_loss)):
        if price is None:
            continue
        leg = _place_protection_leg(req.symbol, close_side, qty, price, kind == "take_profit")
        out[kind] = _norm_order(leg)
        _audit(req.symbol, out[kind]["id"], agent, "protection", order={
            "side": close_side, "type": out[kind]["type"], "qty": qty,
            "reduce_only": True, kind: price})
        for o in old:
            if o["tp_sl"] != kind:
                continue
            try:
                exchange.cancel_order(o["id"], req.symbol, algo=o["algo"])
                replaced.append(o["id"])
                _audit(req.symbol, o["id"], agent, "cancel")
            except Exception:
                cancel_failed.append(o["id"])  # old leg still resting; reduce-only legs can't over-close
    out["replaced"] = replaced
    if cancel_failed:
        out["cancel_failed"] = cancel_failed
    return out
