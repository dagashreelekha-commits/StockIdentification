"""Combine news catalyst (existing aggregate) + momentum signal into tradeable Idea objects.

Entry = next-day open (or current LTP for live). Stop = entry - 1.5 * ATR. Target = entry + 2.5 * ATR.
Size = quarter-Kelly on an edge estimate derived from the composite score; capped at risk_pct of equity.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .aggregate import SectorScore
from .momentum import MomentumSignal
from .universe import ticker_sector_map


@dataclass
class Idea:
    idea_id: str
    strategy: str                 # "momentum_breakout" (future: "pairs", "insider_cluster")
    symbol: str
    sub_sector: str
    entry_hint: float             # suggested entry (last close; real entry = next-bar open or LTP)
    stop: float
    target: float
    atr: float
    momentum_score: float
    news_score: float             # catalyst score for the symbol's sub-sector (0 if no catalyst)
    composite: float              # final rank number
    size_pct: float               # % of equity to risk (quarter-Kelly, capped)
    confidence_pct: int           # 0..100 for the pager
    reason: str
    created_at: str
    tickers: list[str] = field(default_factory=list)  # for compat with the pager template

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, indent=2)


def _kelly_quarter(win_rate: float, win_loss_ratio: float) -> float:
    """f* = p - q/b. Return a quarter of that, clamped to [0, 0.05]."""
    p, q, b = win_rate, 1 - win_rate, max(win_loss_ratio, 0.01)
    f = p - q / b
    return max(0.0, min(0.25 * f, 0.05))


def build_ideas(signals: list[MomentumSignal], sector_scores: list[SectorScore],
                kb, equity: float = 100_000.0, risk_pct_cap: float = 0.01,
                assumed_win_rate: float = 0.52) -> list[Idea]:
    """One Idea per momentum signal; catalyst bump if the symbol's sub-sector has a positive news score."""
    tsmap = ticker_sector_map(kb)
    news_by_sub = {s.sub_sector: s.score for s in sector_scores}
    now_iso = datetime.now(timezone.utc).isoformat()
    out: list[Idea] = []
    for sig in signals:
        sub = tsmap.get(sig.symbol, "")
        catalyst = max(news_by_sub.get(sub, 0.0), 0.0)
        # Composite: momentum first, catalyst second (0..0.3 bump).
        composite = sig.score + min(catalyst, 3.0) * 0.1
        # Reward:risk is baked in at 2.5:1.5; use that for win/loss ratio in Kelly.
        f_q = _kelly_quarter(assumed_win_rate, 2.5 / 1.5)
        # Translate Kelly to a position size that risks at most risk_pct_cap per trade.
        # risk_per_share = 1.5 * ATR. shares = (equity * risk_pct_cap) / risk_per_share.
        # We expose size as a % of equity, not shares -- the launcher converts.
        size_pct = round(min(f_q, risk_pct_cap) * 100, 3)
        stop = round(sig.close - 1.5 * sig.atr_14, 2)
        target = round(sig.close + 2.5 * sig.atr_14, 2)
        idea_id = f"MB-{sig.symbol}-{sig.as_of.isoformat()}"
        reason = sig.reason + (f"; catalyst {sub} score {catalyst:.1f}" if catalyst > 0 else "")
        out.append(Idea(
            idea_id=idea_id, strategy="momentum_breakout", symbol=sig.symbol, sub_sector=sub,
            entry_hint=sig.close, stop=stop, target=target, atr=round(sig.atr_14, 3),
            momentum_score=sig.score, news_score=round(catalyst, 3), composite=round(composite, 3),
            size_pct=size_pct, confidence_pct=int(min(composite * 100, 99)),
            reason=reason, created_at=now_iso, tickers=[sig.symbol],
        ))
    out.sort(key=lambda i: -i.composite)
    return out


def write_ideas(ideas: list[Idea], out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in ideas:
        p = out_dir / f"{i.idea_id}.json"
        p.write_text(i.to_json())
        paths.append(p)
    return paths
