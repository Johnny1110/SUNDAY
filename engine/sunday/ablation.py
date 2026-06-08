"""Ablation harness (milestone-4 T6) — the kill-line (M4-D5).

The one question that decides whether this whole pivot is real: **does the
information layer + agent synthesis add value, or is it expensive theatre?** This
module gives the always-on comparison:

  - **shadow baselines** (no trades, computed over the same tape): `buy_hold`
    (equal-weight basket index — faithful) and `funding_carry` (an *idealised*
    delta-neutral carry yield — directional reference, not a tradeable result).
  - **info-ON/OFF split**: realized PnL grouped by whether a symbol is in the
    `info_off` set, so we can see if the symbols the desk had feeds for did better
    than the ones it flew blind on.

`buy_hold_index` / `carry_step` are PURE (unit-tested). `snapshot_shadows` is the
live edge (called each watcher tick). `build_report` assembles the `/ablation`
body purely. Honest about its first-cut fidelity (see the report `note`).
"""

from __future__ import annotations

import logging

# exchange / store / config.settings are imported lazily inside snapshot_shadows so the
# PURE baseline math + report assembly (buy_hold_index / carry_step / build_report) stays
# importable + unit-testable stdlib-only.

log = logging.getLogger("sunday")

START_CAPITAL = 10_000.0
_FUNDING_INTERVAL_SEC = 8 * 3600  # Binance USDⓈ-M funding settles every 8h


def buy_hold_index(first_prices: dict[str, float], current_prices: dict[str, float],
                   start: float = START_CAPITAL) -> float:
    """Equal-weight buy-hold equity: start × mean(current/first) over symbols seen in both."""
    ratios = [current_prices[s] / first_prices[s]
              for s in current_prices if first_prices.get(s) and current_prices.get(s)]
    return round(start * (sum(ratios) / len(ratios)), 4) if ratios else float(start)


def carry_step(prev_equity: float, funding_yield_per_period: float | None) -> float:
    """Idealised carry accrual for one period (delta-neutral assumption, no price risk)."""
    return round(prev_equity * (1.0 + (funding_yield_per_period or 0.0)), 6)


def snapshot_shadows(symbols: list[str]) -> dict:
    """Compute + persist one shadow point per baseline. Never raises (a shadow hiccup
    must not kill the watcher)."""
    from . import exchange, store
    from .config import settings
    out: dict = {}
    try:
        current = {}
        for s in symbols:
            p = exchange.fetch_last_price(s)
            if p:
                current[s] = p
        if current:
            first = store.get_or_set_first_prices(current)
            bh = buy_hold_index(first, current)
            store.record_shadow("buy_hold", bh)
            out["buy_hold"] = bh
        metrics = store.latest_perp_metrics_all()
        fundings = [abs(m["funding_rate"]) for m in metrics.values() if m.get("funding_rate")]
        if fundings:
            per_period = (sum(fundings) / len(fundings)) * (settings.tick_interval_sec / _FUNDING_INTERVAL_SEC)
            prev = store.last_shadow_equity("funding_carry") or START_CAPITAL
            carry = carry_step(prev, per_period)
            store.record_shadow("funding_carry", carry)
            out["funding_carry"] = carry
    except Exception as e:
        log.warning("shadow snapshot: %s", e)
    return out


def build_report(desk_curve: list, desk_realized: float, shadow_curves: dict[str, list],
                 realized_by_symbol: dict[str, float], theses: list[dict],
                 info_off: list[str]) -> dict:
    """Assemble the /ablation body (pure). info-ON/OFF realized split + shadow curves
    + thesis summary. This is the report the reviewer reads to judge edge (invariant 11)."""
    off = set(info_off)
    on = {s: v for s, v in realized_by_symbol.items() if s not in off}
    off_r = {s: v for s, v in realized_by_symbol.items() if s in off}
    by_status: dict[str, int] = {}
    for t in theses:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    def latest(curve):
        return curve[-1][1] if curve else None
    return {
        "desk": {"realized": round(desk_realized, 4), "equity_latest": latest(desk_curve), "equity_curve": desk_curve},
        "shadows": {
            b: {"equity_latest": latest(c), "equity_curve": c, "start": START_CAPITAL}
            for b, c in shadow_curves.items()
        },
        "info_split": {
            "on": {"symbols": sorted(on), "realized": round(sum(on.values()), 4)},
            "off": {"symbols": sorted(off_r), "realized": round(sum(off_r.values()), 4)},
        },
        "theses": {"n": len(theses), "by_status": by_status},
        "note": ("shadow baselines are no-trade references: buy_hold = faithful equal-weight "
                 "index; funding_carry = idealised delta-neutral yield (no price risk) — "
                 "directional reference only. info-split sample is small with few symbols (M4-D5)."),
    }
