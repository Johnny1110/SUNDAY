"""Strategy signal core — PURE. The decision logic, decoupled from IO.

Given a tape (`Candles`) it returns, per candidate strategy, a `Vote`
(direction + confidence + the indicators behind it + a rationale). It fetches
nothing, writes nothing, reads no globals: the SAME function runs identically
against the live exchange (engine.py feeds it live candles) and against a
historical replay (the backtest feeds it past candles). This is the seam that
makes Gate-2 backtesting test the *real* strategy, not a reimplementation.

The live trading glue (reconcile/open/halt/tick — which touches the exchange,
store and webhooks) lives in `engine.py`, not here. Keep this module import-pure
(only `indicators` + `market`), so the decision logic is unit-testable with the
stdlib alone, anywhere.

Strategy parameters are arguments, not globals — so a backtest can sweep them
(different EMA periods, RSI thresholds) without process-global state.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import indicators as ind
from .market import Candles

# The selectable strategies (the `/strategy` lever's vocabulary).
# `directed` (milestone-4): target comes from the symbol's active thesis, not the tape —
# the engine special-cases it (see engine._reconcile_directed); momentum/mean_reversion
# stay as the ablation baseline + info-OFF comparison.
VALID_STRATEGIES: tuple[str, ...] = ("momentum", "mean_reversion", "flat", "directed")
# The candidates that actually express a market view (flat is the no-position floor).
CANDIDATES: tuple[str, ...] = ("momentum", "mean_reversion")
STRATEGIES = set(VALID_STRATEGIES)  # back-compat alias for callers that membership-test

# Thresholds (tunable; a backtest can override via evaluate()'s params).
MOM_FLAT_SPREAD_PCT = 0.05   # |EMA spread| tighter than this = no real trend → neutral
MR_Z_BAND = 1.0              # close this many std past the mean = a band touch
MR_RSI_OS = 35.0
MR_RSI_OB = 65.0


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


@dataclass
class Vote:
    """One strategy's read of the tape."""
    strategy: str
    vote: str               # "long" | "short" | "neutral"
    confidence: float       # 0..1
    indicators: dict
    rationale: str

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "vote": self.vote,
            "confidence": round(self.confidence, 3),
            "indicators": self.indicators,
            "rationale": self.rationale,
        }


def _momentum(candles: Candles, fast: int, slow: int) -> Vote:
    closes = candles.closes
    ef, es = ind.ema(closes, fast), ind.ema(closes, slow)
    if ef is None or es is None:
        return Vote("momentum", "neutral", 0.0, {}, f"資料不足（需 ≥{slow} 根）")
    spread = (ef - es) / es * 100.0 if es else 0.0
    inds = {f"ema{fast}": round(ef, 2), f"ema{slow}": round(es, 2), "spread_pct": round(spread, 3)}
    if abs(spread) < MOM_FLAT_SPREAD_PCT:
        return Vote("momentum", "neutral", 0.0, inds, f"EMA 糾結（spread {spread:+.2f}%），無趨勢")
    side = "long" if spread > 0 else "short"
    cmp = ">" if side == "long" else "<"
    return Vote("momentum", side, round(_clamp01(abs(spread) / 2.0), 3), inds,
                f"EMA{fast}{cmp}EMA{slow}（spread {spread:+.2f}%）→ 順勢{side}")


def _mean_reversion(candles: Candles) -> Vote:
    closes = candles.closes
    bb, rsi = ind.bollinger(closes, 20, 2.0), ind.rsi(closes, 14)
    if bb is None or rsi is None:
        return Vote("mean_reversion", "neutral", 0.0, {}, "資料不足（需 ≥21 根）")
    z = bb["z"]
    inds = {"bb_z": round(z, 2), "rsi14": round(rsi, 1)}
    conf = round(_clamp01(abs(z) / 2.0), 3)
    if z <= -MR_Z_BAND and rsi <= MR_RSI_OS:
        return Vote("mean_reversion", "long", conf, inds, f"超賣 z={z:.2f}、RSI {rsi:.0f} → 逆勢偏多")
    if z >= MR_Z_BAND and rsi >= MR_RSI_OB:
        return Vote("mean_reversion", "short", conf, inds, f"超買 z={z:.2f}、RSI {rsi:.0f} → 逆勢偏空")
    return Vote("mean_reversion", "neutral", 0.0, inds, f"未觸帶邊 z={z:.2f}、RSI {rsi:.0f}（中性）")


def evaluate(strategy: str, candles: Candles, fast: int = 20, slow: int = 50) -> Vote:
    """The active strategy's read of this tape. Pure; params are injectable."""
    if strategy == "flat":
        return Vote("flat", "neutral", 1.0, {}, "flat：空手（不進場）")
    if strategy == "directed":  # thesis-driven; the engine reads the thesis, not the tape
        return Vote("directed", "neutral", 0.0, {}, "directed：由 thesis 驅動（見引擎 _reconcile_directed）")
    if strategy == "momentum":
        return _momentum(candles, fast, slow)
    if strategy == "mean_reversion":
        return _mean_reversion(candles)
    raise ValueError(f"strategy '{strategy}' not available; valid: {', '.join(VALID_STRATEGIES)}")


def vote_all(candles: Candles) -> list[Vote]:
    """Every candidate strategy's vote — the building block of the /advisor panel."""
    return [evaluate(s, candles) for s in CANDIDATES]
