"""Position-transition planning — the pure "what move does the book need" decision.

The engine loop compares the book's current side to the active strategy's target
side and must decide: hold, open, close, or flip. That branch is easy to get
subtly wrong (a missed flip leaves the book the wrong way round), so it lives here
as a pure function with tests; app.py just executes the returned action against the
exchange under the risk gate.
"""

from __future__ import annotations

# Action vocabulary the loop executes.
HOLD = "hold"
CLOSE = "close"
OPEN_LONG = "open_long"
OPEN_SHORT = "open_short"
FLIP_LONG = "flip_to_long"     # close a short, then open long
FLIP_SHORT = "flip_to_short"   # close a long, then open short


def plan_transition(current_side: str | None, target_side: str | None) -> str:
    """Map (current book side, strategy target side) → the action to take.

    Sides are 'long' | 'short' | None (flat). ``target_side`` None means the active
    strategy wants no position (flat, or a neutral vote)."""
    if current_side == target_side:
        return HOLD                      # already where we want to be (incl. flat↔flat)
    if target_side is None:
        return CLOSE                     # strategy wants out
    if current_side is None:
        return OPEN_LONG if target_side == "long" else OPEN_SHORT
    # holding one side, want the other → flip
    return FLIP_LONG if target_side == "long" else FLIP_SHORT


def is_entry_action(action: str) -> bool:
    """Actions that open/increase exposure (gated by risk.check_order as entries)."""
    return action in (OPEN_LONG, OPEN_SHORT, FLIP_LONG, FLIP_SHORT)
