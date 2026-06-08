"""Deterministic risk fuses — PURE. The final line of defence, never the LLM's job.

Pure functions over plain dataclasses: given an `Envelope` (the hard caps), an
`OrderProposal` and a `RiskContext` (equity + current exposure), decide whether an
order may pass — and whether drawdown has breached. No globals, no store, no
exchange: the SAME fuses run in
live (engine.py logs rejections to risk_events) and in backtest (the sim broker
gates fills through the very same `check_order`), so a backtest can never show a
fill the live engine would have blocked.

This module replaces the old settings/store-coupled `guard()`; it also adds the
drawdown breaker the envelope always specified but the coupled version never
enforced.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class RiskRejected(Exception):
    """An order was blocked by a deterministic fuse (raised at the live boundary)."""


@dataclass(frozen=True)
class Envelope:
    """The hard caps. Set by the leader's /envelope lever; the engine never exceeds it."""
    max_position_usd: float = 2000.0
    max_total_exposure_usd: float = 4000.0
    max_leverage: float = 3.0
    max_drawdown_pct: float = 5.0
    stop_pct: float = 0.02


DEFAULT_ENVELOPE = Envelope()


@dataclass
class OrderProposal:
    symbol: str
    side: str           # buy | sell
    qty: float
    price: float
    has_stop: bool = True
    is_entry: bool = True     # entries open/increase exposure; reduces de-risk and are never blocked

    @property
    def notional(self) -> float:
        return abs(self.qty) * self.price


@dataclass
class RiskContext:
    equity: float
    current_exposure_usd: float = 0.0


@dataclass
class Decision:
    allowed: bool
    violations: list[str] = field(default_factory=list)

    @property
    def type(self) -> str | None:
        """The primary (first) violation, for risk_events tagging."""
        return self.violations[0] if self.violations else None


@dataclass
class DrawdownDecision:
    breached: bool
    drawdown_pct: float
    action: str | None = None    # "flatten_and_lock" on breach


def check_order(order: OrderProposal, ctx: RiskContext, env: Envelope = DEFAULT_ENVELOPE) -> Decision:
    """Gate an order against the envelope. Reduce-only orders always pass (de-risking)."""
    if not order.is_entry:
        return Decision(True, [])
    violations: list[str] = []
    notional = order.notional
    total_after = ctx.current_exposure_usd + notional
    if notional > env.max_position_usd:
        violations.append("size_cap")
    if total_after > env.max_total_exposure_usd:
        violations.append("exposure_cap")
    if ctx.equity > 0 and total_after / ctx.equity > env.max_leverage:
        violations.append("leverage_cap")
    if not order.has_stop:
        violations.append("no_stop")
    return Decision(len(violations) == 0, violations)


def size_from_conviction(conviction: float, price: float, env: Envelope = DEFAULT_ENVELOPE,
                         floor: float = 0.2) -> float:
    """Directed sizing (milestone-4): conviction 0..1 → qty as a fraction of the single-
    position cap. Below the floor → 0 (stay flat). Linear, deterministic, never exceeds
    max_position_usd; the exposure/leverage caps still bind downstream in check_order."""
    if price <= 0 or conviction < floor:
        return 0.0
    notional = min(max(conviction, 0.0), 1.0) * env.max_position_usd
    return round(notional / price, 3)


def drawdown_pct(equity: float, peak_equity: float) -> float:
    """Percent below the high-water mark; 0 at or above the peak."""
    if peak_equity <= 0:
        return 0.0
    return max(0.0, round((peak_equity - equity) / peak_equity * 100.0, 4))


def check_drawdown(equity: float, peak_equity: float, env: Envelope = DEFAULT_ENVELOPE) -> DrawdownDecision:
    """Trip the circuit breaker when drawdown reaches the envelope's limit."""
    dd = drawdown_pct(equity, peak_equity)
    breached = dd >= env.max_drawdown_pct
    return DrawdownDecision(breached, dd, "flatten_and_lock" if breached else None)


def stop_price(side: str, entry: float, stop_pct: float) -> float:
    """Stop below entry for a long, above for a short (pure helper for the engine)."""
    px = entry * (1 - stop_pct) if side == "long" else entry * (1 + stop_pct)
    return round(px, 1)
