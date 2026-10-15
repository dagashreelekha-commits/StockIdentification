"""Decide which backtested strategies clear the bar. Also filters live ideas by the same bar."""
from __future__ import annotations

from dataclasses import dataclass

from .backtest import BacktestResult


@dataclass
class Thresholds:
    min_sharpe: float = 1.0        # lower than the article: Indian retail universe is noisier
    max_drawdown: float = 0.20
    min_win_rate: float = 0.50
    min_t_stat: float = 2.0
    min_trades: int = 30            # don't trust a Sharpe computed on 5 trades
    min_oos_sharpe_ratio: float = 0.5  # OOS Sharpe must be at least 0.5x in-sample Sharpe


def approve(in_sample: BacktestResult, out_sample: BacktestResult, th: Thresholds | None = None) -> tuple[bool, list[str]]:
    th = th or Thresholds()
    reasons: list[str] = []

    def check(cond: bool, msg: str):
        if not cond:
            reasons.append(msg)

    check(in_sample.n_trades >= th.min_trades, f"too few in-sample trades ({in_sample.n_trades} < {th.min_trades})")
    check(in_sample.sharpe >= th.min_sharpe, f"in-sample Sharpe {in_sample.sharpe:.2f} < {th.min_sharpe}")
    check(in_sample.max_drawdown <= th.max_drawdown, f"in-sample DD {in_sample.max_drawdown:.1%} > {th.max_drawdown:.0%}")
    check(in_sample.win_rate >= th.min_win_rate, f"in-sample win rate {in_sample.win_rate:.1%} < {th.min_win_rate:.0%}")
    check(in_sample.t_stat >= th.min_t_stat, f"in-sample t-stat {in_sample.t_stat:.2f} < {th.min_t_stat}")

    if out_sample.n_trades > 0 and in_sample.sharpe > 0:
        ratio = out_sample.sharpe / in_sample.sharpe
        check(ratio >= th.min_oos_sharpe_ratio,
              f"OOS Sharpe {out_sample.sharpe:.2f} is only {ratio:.1%} of IS Sharpe (overfit?)")

    return (not reasons), reasons
