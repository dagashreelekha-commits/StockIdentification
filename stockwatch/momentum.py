"""Momentum / breakout scanner.

For each symbol with enough history, compute a small set of features and emit a MomentumSignal
when a breakout condition fires. Pure Python; no pandas/numpy dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, pstdev

from .broker import Bar, BarCache


@dataclass
class MomentumSignal:
    symbol: str
    as_of: date
    close: float
    breakout_high_20d: float       # 20-day high *before* today's bar
    volume_ratio: float            # today's volume / 20-day avg volume
    dist_from_52w_high: float      # (52w_high - close) / 52w_high; smaller = closer to high
    atr_14: float                  # average true range, 14-day
    rs_30d: float                  # 30-day return minus benchmark 30-day return
    score: float                   # 0..1-ish composite; see score() below
    reason: str                    # one-line why


def _true_range(prev_close: float, high: float, low: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _atr(bars: list[Bar], n: int = 14) -> float:
    if len(bars) < n + 1:
        return 0.0
    trs = [_true_range(bars[i - 1].close, bars[i].high, bars[i].low) for i in range(len(bars) - n, len(bars))]
    return mean(trs)


def _ret(bars: list[Bar], n: int) -> float:
    if len(bars) < n + 1:
        return 0.0
    prev = bars[-n - 1].close
    if prev <= 0:
        return 0.0
    return (bars[-1].close - prev) / prev


def _max(bars: list[Bar], n: int, field: str = "high") -> float:
    window = bars[-n - 1:-1] if len(bars) > n else bars[:-1]
    if not window:
        return 0.0
    return max(getattr(b, field) for b in window)


def _vol_avg(bars: list[Bar], n: int) -> float:
    window = bars[-n - 1:-1] if len(bars) > n else bars[:-1]
    if not window:
        return 0.0
    return mean(b.volume for b in window)


def score(breakout: bool, volume_ratio: float, dist_from_52w: float, rs_30d: float) -> float:
    """Composite in roughly [0, 1]. Weights are reasoned, not fit."""
    s = 0.0
    s += 0.40 if breakout else 0.0
    s += 0.25 * min(volume_ratio / 2.0, 1.0)        # 2x avg vol = full points
    s += 0.20 * max(0.0, 1.0 - dist_from_52w / 0.15)  # within 15% of 52w high
    s += 0.15 * max(0.0, min(rs_30d / 0.10, 1.0))     # 10% outperformance vs index = full
    return round(s, 3)


def analyze_one(symbol: str, bars: list[Bar], bench_30d_ret: float, as_of: date | None = None,
                min_vol_ratio: float = 1.5, min_bars: int = 60) -> MomentumSignal | None:
    """Return a MomentumSignal only if the breakout condition fires."""
    if len(bars) < min_bars:
        return None
    today = bars[-1]
    hi20 = _max(bars, 20, "high")
    if hi20 <= 0:
        return None
    vavg20 = _vol_avg(bars, 20)
    vratio = today.volume / vavg20 if vavg20 > 0 else 0.0
    hi52w = _max(bars, min(252, len(bars) - 1), "high")
    dist52 = (hi52w - today.close) / hi52w if hi52w > 0 else 1.0
    atr = _atr(bars, 14)
    rs = _ret(bars, 30) - bench_30d_ret

    breakout = today.close > hi20 and vratio >= min_vol_ratio
    if not breakout:
        return None

    reason = f"close {today.close:.2f} > 20d high {hi20:.2f}, vol {vratio:.1f}x avg, {dist52*100:.1f}% below 52w high"
    return MomentumSignal(symbol, as_of or today.date, today.close, hi20, vratio,
                          dist52, atr, rs, score(True, vratio, dist52, rs), reason)


def scan(cache: BarCache, symbols: list[str], benchmark_bars: list[Bar],
         min_vol_ratio: float = 1.5, max_dist_52w: float = 0.15) -> list[MomentumSignal]:
    """Scan universe; keep only signals that are breakouts AND within max_dist_52w of 52w high."""
    bench_ret = _ret(benchmark_bars, 30) if benchmark_bars else 0.0
    out: list[MomentumSignal] = []
    for s in symbols:
        bars = cache.get(s)
        sig = analyze_one(s, bars, bench_ret, min_vol_ratio=min_vol_ratio)
        if sig and sig.dist_from_52w_high <= max_dist_52w:
            out.append(sig)
    out.sort(key=lambda s: -s.score)
    return out


def backtest_bars_cutoff(bars: list[Bar], d: date) -> list[Bar]:
    """Bars with date <= d. For walk-forward backtest and held-out validation."""
    return [b for b in bars if b.date <= d]
