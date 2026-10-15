"""Simple historical backtest of the momentum_breakout rule.

For each symbol in the universe, replay the daily bars; on every bar check the entry rule using
only data up to and including that bar; if it fires, enter at the *next* bar's open, exit at the
first of: target hit (2.5*ATR), stop hit (1.5*ATR), or max_hold bars elapsed. Round-trip cost is
charged at `cost_bps` of entry+exit notional (STT + brokerage + slippage).

Pass `held_out_days > 0` to run two separate reports: in-sample (older) and out-of-sample (the
last N days). A strategy that only works in-sample is overfit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from math import sqrt
from statistics import mean, pstdev

from .broker import Bar, BarCache
from .momentum import _atr, _max, _ret, _vol_avg, analyze_one


@dataclass
class Trade:
    symbol: str
    entry_date: date
    exit_date: date
    entry: float
    exit: float
    reason: str                # "target" | "stop" | "time"
    ret_net: float             # net return after costs


@dataclass
class BacktestResult:
    label: str
    trades: list[Trade] = field(default_factory=list)
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_hold_days: float = 0.0
    t_stat: float = 0.0
    n_trades: int = 0

    def to_dict(self) -> dict:
        return {
            "label": self.label, "n_trades": self.n_trades, "sharpe": round(self.sharpe, 3),
            "max_drawdown": round(self.max_drawdown, 4), "win_rate": round(self.win_rate, 3),
            "avg_hold_days": round(self.avg_hold_days, 2), "t_stat": round(self.t_stat, 3),
        }


def _simulate_symbol(symbol: str, bars: list[Bar], benchmark_bars: list[Bar],
                     cost_bps: float = 30.0, max_hold: int = 10,
                     min_vol_ratio: float = 1.5, max_dist_52w: float = 0.15) -> list[Trade]:
    """Walk the bars; emit at most one trade at a time (no pyramiding)."""
    trades: list[Trade] = []
    i = 60  # need warm-up
    bench_dates = {b.date: idx for idx, b in enumerate(benchmark_bars)}
    while i < len(bars) - 1:
        prefix = bars[: i + 1]
        bench_prefix_30d = 0.0
        if benchmark_bars:
            d = prefix[-1].date
            if d in bench_dates and bench_dates[d] >= 30:
                bidx = bench_dates[d]
                prev = benchmark_bars[bidx - 30].close
                bench_prefix_30d = (benchmark_bars[bidx].close - prev) / prev if prev > 0 else 0.0
        sig = analyze_one(symbol, prefix, bench_prefix_30d,
                          min_vol_ratio=min_vol_ratio, min_bars=60)
        if sig is None or sig.dist_from_52w_high > max_dist_52w:
            i += 1
            continue
        entry_bar = bars[i + 1]
        entry = entry_bar.open
        stop = entry - 1.5 * sig.atr_14
        target = entry + 2.5 * sig.atr_14
        exit_price, exit_date, reason = None, None, None
        for j in range(i + 1, min(i + 1 + max_hold, len(bars))):
            b = bars[j]
            # Target first if gap-up through it; else stop if gap-down through it; else intra-bar.
            if b.open >= target:
                exit_price, exit_date, reason = b.open, b.date, "target"; break
            if b.open <= stop:
                exit_price, exit_date, reason = b.open, b.date, "stop"; break
            if b.high >= target:
                exit_price, exit_date, reason = target, b.date, "target"; break
            if b.low <= stop:
                exit_price, exit_date, reason = stop, b.date, "stop"; break
        if exit_price is None:
            last = bars[min(i + max_hold, len(bars) - 1)]
            exit_price, exit_date, reason = last.close, last.date, "time"
        gross = (exit_price - entry) / entry
        net = gross - cost_bps / 1e4
        trades.append(Trade(symbol, entry_bar.date, exit_date, entry, exit_price, reason, net))
        # Skip to the day after exit so we don't re-enter the same trade.
        i = next((k for k, b in enumerate(bars) if b.date > exit_date), len(bars))
    return trades


def _stats(trades: list[Trade], label: str) -> BacktestResult:
    if not trades:
        return BacktestResult(label=label)
    rets = [t.ret_net for t in trades]
    mu, sigma = mean(rets), pstdev(rets) if len(rets) > 1 else 0.0
    # Annualise using avg trades/year. Daily-bar backtest with 10d max hold -> rough proxy.
    span_days = max((trades[-1].exit_date - trades[0].entry_date).days, 1)
    trades_per_year = len(trades) * 365 / span_days
    sharpe = (mu / sigma) * sqrt(trades_per_year) if sigma > 0 else 0.0
    # Max drawdown on equity curve.
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, (eq - peak) / peak)
    win_rate = sum(1 for r in rets if r > 0) / len(rets)
    avg_hold = mean((t.exit_date - t.entry_date).days for t in trades)
    t_stat = (mu / (sigma / sqrt(len(rets)))) if sigma > 0 else 0.0
    return BacktestResult(label=label, trades=trades, sharpe=sharpe, max_drawdown=abs(mdd),
                          win_rate=win_rate, avg_hold_days=avg_hold, t_stat=t_stat, n_trades=len(trades))


def run_backtest(cache: BarCache, symbols: list[str], benchmark_bars: list[Bar],
                 held_out_days: int = 60, cost_bps: float = 30.0) -> tuple[BacktestResult, BacktestResult]:
    """Return (in_sample, out_of_sample)."""
    all_trades: list[Trade] = []
    for s in symbols:
        bars = cache.get(s)
        all_trades.extend(_simulate_symbol(s, bars, benchmark_bars, cost_bps=cost_bps))
    if not all_trades:
        return BacktestResult("in_sample"), BacktestResult("out_of_sample")
    all_trades.sort(key=lambda t: t.entry_date)
    cutoff = all_trades[-1].entry_date - timedelta(days=held_out_days)
    in_sample = [t for t in all_trades if t.entry_date <= cutoff]
    out_sample = [t for t in all_trades if t.entry_date > cutoff]
    return _stats(in_sample, "in_sample"), _stats(out_sample, "out_of_sample")
