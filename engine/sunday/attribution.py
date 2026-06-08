"""Per-switch outcome attribution — the closed loop (PRD §2.1, milestone-3 T3).

This is the substrate that turns supervision from open-loop ("switch strategy and
hope") into closed-loop ("learn which switch, in which regime, paid off"). The PRD
is explicit that Gate-2's only credible alpha lives in the *switching policy*, and
a policy can only be learned if each switch's outcome is captured and made legible.

It is a PURE function over rows the store already has (M3-D3: a lens, not new
capture). Each ``strategy_state`` row opens an *episode* that runs until the next
switch on that symbol; the episode's outcome is the realized result of the
positions opened during its window. ``store`` feeds rows in, JSON goes out via
``GET /strategy/outcomes``.

Timestamps are compared as opaque orderable values (epoch ints or datetimes), so
this stays tz-free and unit-testable; the store hands in whatever it reads.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Episode:
    symbol: str
    strategy: str
    set_by: str
    reason: str | None
    set_at: object             # orderable (epoch int or datetime)
    ended_at: object | None    # next switch's set_at, or None if still current
    realized_pnl: float
    trades: int                # closed positions attributed to this episode
    open_trades: int           # still-open positions opened in this window
    win_rate: float            # over closed trades (0..1)
    deployed_usd: float        # entry notional of the closed trades
    return_pct: float          # realized_pnl / deployed_usd * 100

    def as_dict(self) -> dict:
        def ts(v):
            return v.isoformat() if hasattr(v, "isoformat") else v
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "set_by": self.set_by,
            "reason": self.reason,
            "set_at": ts(self.set_at),
            "ended_at": ts(self.ended_at),
            "realized_pnl": round(self.realized_pnl, 4),
            "trades": self.trades,
            "open_trades": self.open_trades,
            "win_rate": round(self.win_rate, 4),
            "deployed_usd": round(self.deployed_usd, 2),
            "return_pct": round(self.return_pct, 4),
        }


def _in_window(opened_at, start, end) -> bool:
    if opened_at < start:
        return False
    return end is None or opened_at < end


def attribute(switches: list[dict], positions: list[dict]) -> list[Episode]:
    """Attribute closed-position PnL to the strategy episode that owned its open.

    ``switches``  : strategy_state rows, each {strategy, set_by, reason, set_at, symbol?}.
    ``positions`` : rows {strategy, qty, entry_price, realized_pnl, opened_at, closed_at}.
                    ``closed_at is None`` ⇒ still open (counted, not realized).
    Returns episodes oldest-first (callers slice ``[-N:]`` for the recent page).
    """
    if not switches:
        return []
    ordered = sorted(switches, key=lambda s: s["set_at"])
    episodes: list[Episode] = []

    for i, sw in enumerate(ordered):
        start = sw["set_at"]
        end = ordered[i + 1]["set_at"] if i + 1 < len(ordered) else None

        realized = 0.0
        deployed = 0.0
        closed = 0
        wins = 0
        open_n = 0
        for p in positions:
            if not _in_window(p["opened_at"], start, end):
                continue
            if p.get("closed_at") is None:
                open_n += 1
                continue
            closed += 1
            pnl = float(p.get("realized_pnl") or 0.0)
            realized += pnl
            deployed += abs(float(p["qty"])) * float(p["entry_price"])
            if pnl > 0:
                wins += 1

        episodes.append(Episode(
            symbol=sw.get("symbol", positions[0]["symbol"] if positions and "symbol" in positions[0] else ""),
            strategy=sw["strategy"],
            set_by=sw.get("set_by", ""),
            reason=sw.get("reason"),
            set_at=start,
            ended_at=end,
            realized_pnl=realized,
            trades=closed,
            open_trades=open_n,
            win_rate=(wins / closed) if closed else 0.0,
            deployed_usd=deployed,
            return_pct=(realized / deployed * 100.0) if deployed else 0.0,
        ))
    return episodes
