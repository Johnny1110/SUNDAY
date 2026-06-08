"""Deterministic risk circuit breakers — the safety fuse (PRD §7.3, invariant #7).

Hard limits live HERE, in plain Python, never in an LLM. This is the final line
the PRD leans on repeatedly: even if an agent (or a bug) asks for something out of
bounds, ``check_order`` rejects it before it reaches the exchange. It is pure and
exhaustively tested so the guarantee is real, not aspirational (V6 requires a
demonstrated "over-limit order rejected").

Two surfaces:
  * ``check_order``  — gate every *entry/increase* against the envelope. Reducing
    or closing orders de-risk, so they are never blocked.
  * ``check_drawdown`` — the equity-curve breaker: at/over ``max_drawdown_pct`` it
    reports a breach so the loop can flatten + lock new entries + fire risk_breach.

``max_allowed_qty`` lets the executor *size within* the envelope up front; the
gate is then defense-in-depth (it must still pass even if sizing is wrong).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Envelope:
    """The hard boundary Sunday must never cross. Gate-1 defaults from PRD §8;
    in 1.1 the leader sets it via POST /envelope."""
    max_position_usd: float = 2000.0
    max_total_exposure_usd: float = 4000.0
    max_leverage: float = 3.0
    max_drawdown_pct: float = 5.0
    stop_pct: float = 2.0

    def as_dict(self) -> dict:
        return asdict(self)

    # The fields a leader may set via /envelope (all of them; each a hard bound).
    FIELDS = ("max_position_usd", "max_total_exposure_usd", "max_leverage", "max_drawdown_pct", "stop_pct")


DEFAULT_ENVELOPE = Envelope()


@dataclass
class OrderProposal:
    symbol: str
    side: str            # "buy" | "sell"
    qty: float           # > 0
    price: float         # mark/limit used for notional
    has_stop: bool = False
    is_entry: bool = True  # entries/increases are gated; reduces/closes de-risk

    @property
    def notional(self) -> float:
        return abs(self.qty) * self.price


@dataclass
class RiskContext:
    equity: float
    # exposure (USD notional) of all OPEN positions OTHER than the one this order
    # opens/increases; the order's own notional is added on top for the caps.
    current_exposure_usd: float = 0.0


@dataclass
class Decision:
    allowed: bool
    reason: str
    violations: list[str] = field(default_factory=list)

    @property
    def type(self) -> str | None:
        """First violation code, for the risk_events ledger."""
        return self.violations[0] if self.violations else None


def check_order(p: OrderProposal, ctx: RiskContext, env: Envelope = DEFAULT_ENVELOPE) -> Decision:
    """Allow or reject an order against the envelope. Deterministic; no LLM."""
    # De-risking orders (reduce/close) always pass — they lower exposure.
    if not p.is_entry:
        return Decision(True, "reduce/close：降風險，放行")

    violations: list[str] = []
    notional = p.notional
    total_after = ctx.current_exposure_usd + notional

    if notional > env.max_position_usd:
        violations.append("size_cap")
    if total_after > env.max_total_exposure_usd:
        violations.append("exposure_cap")
    if ctx.equity > 0 and (total_after / ctx.equity) > env.max_leverage:
        violations.append("leverage_cap")
    if not p.has_stop:
        violations.append("no_stop")

    if violations:
        parts = {
            "size_cap": f"單筆 ${notional:.0f} > 上限 ${env.max_position_usd:.0f}",
            "exposure_cap": f"總曝險 ${total_after:.0f} > 上限 ${env.max_total_exposure_usd:.0f}",
            "leverage_cap": f"槓桿 {total_after / ctx.equity:.2f}x > 上限 {env.max_leverage:.1f}x" if ctx.equity > 0 else "權益為 0，無法評估槓桿",
            "no_stop": "進場未掛 stop",
        }
        reason = "拒單：" + "；".join(parts[v] for v in violations)
        return Decision(False, reason, violations)

    return Decision(True, "在封套內，放行")


def max_allowed_qty(price: float, ctx: RiskContext, env: Envelope = DEFAULT_ENVELOPE) -> float:
    """Largest entry qty that fits every cap at ``price`` (0 if no room)."""
    if price <= 0:
        return 0.0
    by_size = env.max_position_usd / price
    by_exposure = max(0.0, env.max_total_exposure_usd - ctx.current_exposure_usd) / price
    by_leverage = max(0.0, env.max_leverage * ctx.equity - ctx.current_exposure_usd) / price if ctx.equity > 0 else 0.0
    return max(0.0, min(by_size, by_exposure, by_leverage))


def drawdown_pct(equity: float, peak_equity: float) -> float:
    """Percent drop from the equity peak (0 when at/above peak)."""
    if peak_equity <= 0:
        return 0.0
    return max(0.0, (peak_equity - equity) / peak_equity * 100.0)


@dataclass
class DrawdownDecision:
    breached: bool
    drawdown_pct: float
    reason: str
    action: str | None = None  # "flatten_and_lock" when breached


def check_drawdown(equity: float, peak_equity: float, env: Envelope = DEFAULT_ENVELOPE) -> DrawdownDecision:
    """The equity-curve breaker. At/over the limit → deterministic flatten + lock."""
    dd = drawdown_pct(equity, peak_equity)
    if dd >= env.max_drawdown_pct:
        return DrawdownDecision(
            True, dd,
            f"回撤 {dd:.2f}% ≥ 上限 {env.max_drawdown_pct:.1f}% → 確定性減倉並鎖新倉",
            action="flatten_and_lock",
        )
    return DrawdownDecision(False, dd, f"回撤 {dd:.2f}%（< {env.max_drawdown_pct:.1f}%）")
