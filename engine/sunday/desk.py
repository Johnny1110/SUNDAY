"""Desk panel + notable score (milestone-4 T2) — the research desk's "where to look".

`/desk` is the agent's first stop: a basket-wide summary of the batch-1 perp signals
(funding / basis / OI Δ) with a **notable score** per symbol — Sunday's cheap,
continuous read of "how unusual is this symbol right now". When a symbol's score
crosses the wake threshold (and it's a *fresh* crossing — debounced like
`regime.is_shift`), Sunday emits a webhook to wake the desk on that symbol. This is
the event-gating that keeps the swarm asleep when nothing's happening (PRD §5 / §6).

Scoring is PURE (unit-testable, no exchange/DB). The live `check_notable_and_notify`
reads stored metrics, debounces, and emits — no candle fetch (it reuses what the
watcher already ingested), so it's cheap to run every tick.
"""

from __future__ import annotations

import logging

# events / store / config.settings are imported lazily inside check_notable_and_notify
# so the PURE scoring (notable_score / build_basket / …) stays importable stdlib-only.

log = logging.getLogger("sunday")

# Notable thresholds (tunable; M4-D4). Each raw signal is normalised against its
# threshold (→ 0..1), then weighted. A symbol wakes the desk when score ≥ WAKE.
FUNDING_HOT_ANNUAL = 50.0   # |annualised funding| ≥ 50%/yr = a meaningful tilt
BASIS_WIDE_BPS = 40.0       # |basis| ≥ 40 bps = a stretched perp-spot gap
OI_JUMP_PCT = 8.0           # |ΔOI| ≥ 8% over the window = positioning building/unwinding
VOL_HOT_PCT = 2.5           # realised vol (matches regime.VOL_HOT_PCT)
WAKE = 0.5

_WEIGHTS = {"funding": 0.40, "oi": 0.25, "basis": 0.15, "vol": 0.20}
_DRIVER_EVENT = {"funding": "funding_extreme", "oi": "oi_surge",
                 "basis": "basis_stretch", "vol": "vol_spike"}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def oi_change_pct(recent: list[dict]) -> float | None:
    """Percent change of open interest across a newest-first window (latest vs oldest)."""
    vals = [r["open_interest"] for r in recent if r.get("open_interest")]
    if len(vals) < 2 or not vals[-1]:
        return None
    latest, oldest = vals[0], vals[-1]
    return round((latest - oldest) / oldest * 100.0, 2)


def notable_score(funding_annual_pct: float | None, basis_bps: float | None,
                  oi_chg_pct: float | None, vol_pct: float | None = None) -> tuple[float, str]:
    """Pure: weighted 0..1 notability + the dominant driver. Weights renormalise over
    whichever signals are present (testnet often omits some), so a symbol isn't
    penalised for missing feeds."""
    raw = {
        "funding": _clamp01(abs(funding_annual_pct) / FUNDING_HOT_ANNUAL) if funding_annual_pct is not None else None,
        "basis": _clamp01(abs(basis_bps) / BASIS_WIDE_BPS) if basis_bps is not None else None,
        "oi": _clamp01(abs(oi_chg_pct) / OI_JUMP_PCT) if oi_chg_pct is not None else None,
        "vol": _clamp01(vol_pct / VOL_HOT_PCT) if vol_pct is not None else None,
    }
    present = {k: v for k, v in raw.items() if v is not None}
    if not present:
        return 0.0, "none"
    wsum = sum(_WEIGHTS[k] for k in present)
    score = sum(_WEIGHTS[k] * v for k, v in present.items()) / wsum
    driver = max(present, key=lambda k: _WEIGHTS[k] * present[k])
    return round(score, 3), driver


def symbol_summary(symbol: str, metrics: dict | None, oi_chg: float | None,
                   vol_pct: float | None = None) -> dict:
    """One basket row: the metrics + computed notable score/driver (for /desk)."""
    m = metrics or {}
    score, driver = notable_score(m.get("funding_annual_pct"), m.get("basis_bps"), oi_chg, vol_pct)
    return {
        "symbol": symbol,
        "funding_rate": m.get("funding_rate"),
        "funding_annual_pct": m.get("funding_annual_pct"),
        "open_interest": m.get("open_interest"),
        "oi_change_pct": oi_chg,
        "basis_bps": m.get("basis_bps"),
        "notable": score,
        "driver": driver if score > 0 else None,
        "ts": m.get("ts"),
    }


def build_basket(symbols: list[str], metrics_map: dict[str, dict],
                 recent_map: dict[str, list[dict]], info_off: list[str] | None = None) -> list[dict]:
    """The /desk basket panel: one summary per symbol, most-notable first.
    info-OFF symbols (ablation) are tagged so the UI/agents know they get no feeds."""
    off = set(info_off or [])
    rows = []
    for sym in symbols:
        row = symbol_summary(sym, metrics_map.get(sym), oi_change_pct(recent_map.get(sym, [])))
        row["info_mode"] = "off" if sym in off else "on"
        rows.append(row)
    rows.sort(key=lambda r: r["notable"], reverse=True)
    return rows


# --- live: notable-score wake (called from the watcher tick) ---------------

def check_notable_and_notify(symbols: list[str]) -> dict:
    """Read stored metrics, score each symbol, and emit a webhook on a fresh threshold
    crossing (debounced per symbol). info-OFF symbols (ablation) never wake the desk.
    Never raises — a desk hiccup must not kill the watcher."""
    from . import events, store
    from .config import settings
    fired: dict[str, str] = {}
    try:
        metrics_map = store.latest_perp_metrics_all()
    except Exception as e:
        log.warning("desk metrics read: %s", e)
        return fired
    off = set(settings.info_off_list)
    for sym in symbols:
        if sym in off:
            continue
        try:
            m = metrics_map.get(sym)
            if not m:
                continue
            oi_chg = oi_change_pct(store.perp_metrics_recent(sym, 30))
            score, driver = notable_score(m.get("funding_annual_pct"), m.get("basis_bps"), oi_chg)
            prev = store.get_last_notable(sym)
            if score >= WAKE and driver != prev:
                event_type = _DRIVER_EVENT.get(driver, "notable")
                from . import engine  # local import: engine.live() builds the ccxt/store edge
                engine.live().notify(events.notable_event(sym, event_type, driver, score, m))
                store.set_last_notable(sym, driver)
                fired[sym] = driver
            elif score < WAKE and prev is not None:
                store.set_last_notable(sym, None)  # reset so a later re-cross re-fires
        except Exception as e:
            log.warning("desk notable %s: %s", sym, e)
    return fired
