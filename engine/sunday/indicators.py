"""Pure-stdlib technical indicators for Sunday's Gate-1 strategies.

Gate-1 strategies are deliberately simple (PRD §7.2), so the indicator set is
small and implemented in plain Python — no numpy/pandas. Two reasons:

1. It keeps the engine light (Gate-1 doesn't need a dataframe stack), and
2. every signal becomes unit-testable in any environment (stdlib only).

The SAME functions back both the live strategy decision (``strategy.py``) and the
``/advisor`` decision-support panel (via ``advisor.py``), so an agent reasons over exactly
the numbers the engine acted on — that DRY is what makes the panel honest
(PRD §7.9 legibility). pandas/numpy return as a Gate-2 *modeling* extra, not here.

All functions take plain ``list[float]`` (oldest-first) and return ``None`` when
there isn't enough data rather than raising — callers treat "not enough bars" as
"no opinion", which is the correct trading stance on a cold series.
"""

from __future__ import annotations


def sma(values: list[float], period: int) -> float | None:
    """Simple moving average of the last ``period`` values."""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    """Exponential moving average, seeded with the SMA of the first window.

    Seeding with the SMA (rather than the first sample) matches common charting
    platforms and removes the cold-start bias of a single-point seed; on a series
    longer than a few ``period``s the choice is immaterial, but pinning it makes
    the value reproducible and testable.
    """
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period  # seed = SMA of first `period`
    for v in values[period:]:
        e = v * k + e * (1.0 - k)
    return e


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI in [0, 100]. Needs ``period + 1`` closes."""
    if period <= 0 or len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):  # Wilder smoothing
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def bollinger(closes: list[float], period: int = 20, k: float = 2.0) -> dict | None:
    """Bollinger bands + the latest close's z-score (population std).

    ``z`` (how many std the last close sits from the mean) is what a
    mean-reversion strategy actually reads, so it is returned alongside the bands.
    """
    if period <= 0 or len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    sd = var ** 0.5
    last = closes[-1]
    z = 0.0 if sd == 0.0 else (last - mid) / sd
    return {
        "mid": mid,
        "upper": mid + k * sd,
        "lower": mid - k * sd,
        "sd": sd,
        "z": z,
    }


def true_ranges(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    """Per-bar true range (index i aligns to bar i, starting at i=1)."""
    trs: list[float] = []
    for i in range(1, len(closes)):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    return trs


def _wilder_smooth(xs: list[float], period: int) -> list[float]:
    """Wilder's running sum smoothing: seed = sum of first `period`, then
    ``s = s - s/period + x``. Returns the smoothed series (len = len(xs)-period+1)."""
    if len(xs) < period:
        return []
    s = sum(xs[:period])
    out = [s]
    for i in range(period, len(xs)):
        s = s - s / period + xs[i]
        out.append(s)
    return out


def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """Wilder's ADX (trend strength, 0–100). Needs ~``2*period+1`` bars.

    High ADX (>~25) = strong trend → momentum's regime; low ADX (<~20) = chop →
    mean-reversion's regime. regime.py reads this to classify the tape.
    """
    n = len(closes)
    if n < 2 * period + 1 or len(highs) != n or len(lows) != n:
        return None
    trs = true_ranges(highs, lows, closes)
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0.0) else 0.0)
        minus_dm.append(down if (down > up and down > 0.0) else 0.0)

    str_ = _wilder_smooth(trs, period)
    sp = _wilder_smooth(plus_dm, period)
    sm = _wilder_smooth(minus_dm, period)

    dxs: list[float] = []
    for i in range(len(str_)):
        if str_[i] == 0.0:
            dxs.append(0.0)
            continue
        pdi = 100.0 * sp[i] / str_[i]
        mdi = 100.0 * sm[i] / str_[i]
        denom = pdi + mdi
        dxs.append(0.0 if denom == 0.0 else 100.0 * abs(pdi - mdi) / denom)

    if len(dxs) < period:
        return None
    adx_val = sum(dxs[:period]) / period
    for i in range(period, len(dxs)):
        adx_val = (adx_val * (period - 1) + dxs[i]) / period
    return adx_val
