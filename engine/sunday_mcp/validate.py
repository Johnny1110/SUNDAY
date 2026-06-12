"""Cross-field validation for the write tools (PRD-9.3 §2, S7 stdlib-only).

These are the rules JSON Schema can't express. They run as pure functions
BEFORE any upstream call, so a rejected input has zero side effects, and each
validator returns ALL violations at once — the agent fixes everything in one
round trip, not one error per attempt.

Scope discipline (the drift guard from PRD-9.3 §6): only STRUCTURE lives here
— exactly-one, field pairing, relative direction. Anything that needs market
state (trigger fire zones, precision, limits) is the engine's job; the sidecar
holds no market view (S1), so those rules exist on exactly one side.
"""

from __future__ import annotations


def order_violations(side: str, type: str, qty: float | None,
                     notional_usd: float | None, price: float | None,
                     take_profit: float | None, stop_loss: float | None) -> list[str]:
    """place_order cross-rules. TP/SL are schema-required; the None guards keep
    this function total for direct (unit-test) callers."""
    v: list[str] = []
    if (qty is None) == (notional_usd is None):
        v.append("give exactly one of qty / notional_usd")
    if type == "limit" and price is None:
        v.append("limit order requires price")
    if type == "market" and price is not None:
        v.append("market order must not carry price (it fills at market)")
    if take_profit is not None and stop_loss is not None:
        if side == "buy" and take_profit <= stop_loss:
            v.append("for buy: take_profit must be above stop_loss")
        if side == "sell" and take_profit >= stop_loss:
            v.append("for sell: take_profit must be below stop_loss")
    return v


def protection_violations(take_profit: float | None, stop_loss: float | None) -> list[str]:
    """set_protection: a call that changes nothing is a mistake, not a no-op.
    (TP-vs-SL direction needs the position's side — engine-side knowledge.)"""
    if take_profit is None and stop_loss is None:
        return ["provide take_profit and/or stop_loss (a null leaves that leg as-is)"]
    return []


def leverage_margin_violations(leverage: int | None, margin_mode: str | None) -> list[str]:
    if leverage is None and margin_mode is None:
        return ["provide leverage and/or margin_mode"]
    return []


def alerts_violations(action: str, id: int | None) -> list[str]:
    if action == "delete" and id is None:
        return ["action=delete requires id (find it via action=list)"]
    return []
