"""Regime detection — the cheap, continuous market read that decides *when* a
strategy switch is even worth an agent's attention (PRD §5: Sunday gates wakes).

``classify`` turns a tape into a regime label + the indicators behind it; the
engine loop tracks the last *emitted* label and fires a ``regime_shift`` webhook
only when ``is_shift`` says it really changed (hysteresis/debounce lives in the
loop, so this module stays pure and testable). The rationale string is carried
into both ``/advisor`` and the webhook payload — legibility is a hard requirement
(PRD §7.9): the agent must see *why* the engine thinks the regime turned, not just
a label.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import indicators as ind
from .market import Candles

# Gate-1 thresholds (tunable; PRD §12.1 leaves exact numbers open).
ADX_TREND = 25.0     # ADX at/above this = a real trend
VOL_HOT_PCT = 2.5    # hourly realized vol at/above this = "volatile" regardless of trend


@dataclass
class RegimeRead:
    label: str               # "trending" | "ranging" | "volatile" | "unknown"
    adx: float | None
    vol_pct: float | None
    rationale: str

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "adx": round(self.adx, 2) if self.adx is not None else None,
            "vol_pct": round(self.vol_pct, 3) if self.vol_pct is not None else None,
            "rationale": self.rationale,
        }


def realized_vol_pct(closes: list[float], period: int = 20) -> float | None:
    """Population stdev of the last ``period`` simple returns, in percent."""
    if len(closes) < period + 1:
        return None
    rets = []
    for i in range(len(closes) - period, len(closes)):
        prev = closes[i - 1]
        if prev == 0:
            continue
        rets.append((closes[i] - prev) / prev * 100.0)
    if not rets:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return var ** 0.5


def classify(c: Candles, adx_trend: float = ADX_TREND, vol_hot: float = VOL_HOT_PCT) -> RegimeRead:
    a = ind.adx(c.highs, c.lows, c.closes, 14)
    v = realized_vol_pct(c.closes, 20)
    if a is None or v is None:
        return RegimeRead("unknown", a, v, "資料不足，無法判定盤性")
    if v >= vol_hot:
        return RegimeRead("volatile", a, v, f"波動率 {v:.2f}%（≥{vol_hot}%）→ 高波動，慎開倉")
    if a >= adx_trend:
        return RegimeRead("trending", a, v, f"ADX {a:.1f}（≥{adx_trend}）→ 趨勢盤，宜順勢（momentum）")
    return RegimeRead("ranging", a, v, f"ADX {a:.1f}（<{adx_trend}）、波動 {v:.2f}% → 震盪盤，宜逆勢（mean_reversion）")


def is_shift(prev_label: str | None, curr_label: str) -> bool:
    """A regime_shift worth waking an agent: a real change to a known label.

    ``unknown`` never triggers (cold/insufficient data isn't a regime change), and
    the first classification (prev is None) doesn't either — there's no "from"."""
    return prev_label is not None and curr_label != "unknown" and curr_label != prev_label
