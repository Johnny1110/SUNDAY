"""advisor.py — the agent's decision-support panel (the "best tool to make money").

Pure + unit-testable. Given a tape (+ the perp funding rate), it returns, for one
symbol:

- the **regime** read (trending / ranging / volatile) from ADX + realized vol,
- each candidate strategy's **vote** (momentum / mean_reversion) — taken straight
  from `strategy.py`, the SAME pure core the live engine trades on, so the panel
  can never disagree with what a switch to that strategy would actually do,
- the **funding context** (a perps-specific edge: who pays whom, annualised),
- a data-driven **recommendation** (which strategy the regime + signals + funding
  favour, and why).

friday/analyst read this (`GET /advisor`) to decide *whether to switch and to what*
over the engine's own computed features — instead of eyeballing raw OHLCV. Per the
PRD (§2.1) the swarm's alpha lives in the *switching policy*, so this panel is the
single highest-leverage tool to invest in. It never trades; it only informs.
"""

from __future__ import annotations

from . import regime as rg
from . import strategy as strat
from .market import Candles

# Binance USDⓈ-M funding settles every 8h → 3×/day. These gate the funding "bias".
_FUNDING_HOT = 0.0005    # |rate| ≥ this (0.05%/8h ≈ 55%/yr) is a meaningful tilt


def funding_context(funding_rate: float | None) -> dict:
    """Interpret the perp funding rate (per-8h fraction). Positive = longs pay shorts."""
    if funding_rate is None:
        return {"rate": None, "annualized_pct": None, "bias": "unknown",
                "note": "無 funding 資料"}
    annual = funding_rate * 3 * 365 * 100.0
    if funding_rate >= _FUNDING_HOT:
        bias, note = "favors_short", f"多單付高 funding（年化 {annual:+.0f}%），持多成本高、偏空有利"
    elif funding_rate <= -_FUNDING_HOT:
        bias, note = "favors_long", f"空單付 funding（年化 {annual:+.0f}%），持多反而收 funding、偏多有利"
    else:
        bias, note = "neutral", f"funding 中性（年化 {annual:+.0f}%）"
    return {"rate": round(funding_rate, 6), "annualized_pct": round(annual, 1), "bias": bias, "note": note}


def recommend(regime_label: str, votes: list[dict], funding: dict) -> dict:
    """Data-driven hint: which strategy the regime + signals + funding favour."""
    by_name = {v["strategy"]: v for v in votes}
    if regime_label == "volatile":
        return {"strategy": "flat", "direction": None,
                "why": "高波動盤 → 建議 flat 觀望，避免被掃"}
    if regime_label == "trending":
        mv = by_name.get("momentum", {})
        rec = {"strategy": "momentum", "direction": mv.get("vote"),
               "why": f"趨勢盤（{mv.get('rationale', '')}）"}
    else:  # ranging / unknown
        rv = by_name.get("mean_reversion", {})
        if rv.get("vote") in ("long", "short"):
            rec = {"strategy": "mean_reversion", "direction": rv.get("vote"),
                   "why": f"震盪盤且觸帶邊（{rv.get('rationale', '')}）"}
        else:
            rec = {"strategy": "flat", "direction": None,
                   "why": "震盪盤但未觸帶邊 → 無明確 edge，建議 flat 等訊號"}
    # funding caveat: warn when the recommended direction fights the funding tilt
    d = rec.get("direction")
    if d == "long" and funding.get("bias") == "favors_short":
        rec["funding_caveat"] = funding["note"]
    elif d == "short" and funding.get("bias") == "favors_long":
        rec["funding_caveat"] = funding["note"]
    return rec


def advise(candles: Candles, funding_rate: float | None, active: str,
           symbol: str = "", fast: int = 20, slow: int = 50) -> dict:
    """Assemble the full decision-support panel (the /advisor body).

    Votes come from `strategy.evaluate` — the one pure core the live engine also
    decides on — so the panel and the actual trade can never drift apart."""
    regime = rg.classify(candles)
    votes = [strat.evaluate("momentum", candles, fast, slow).as_dict(),
             strat.evaluate("mean_reversion", candles).as_dict()]
    funding = funding_context(funding_rate)
    return {
        "symbol": symbol or "",
        "active": active,
        "regime": regime.as_dict(),
        "funding": funding,
        "votes": votes,
        "recommendation": recommend(regime.label, votes, funding),
    }
