"""Turn per-headline impacts into a ranked sub-sector view with time decay."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import Impact, NewsItem

HORIZON_BOOST = {"intraday": 0.8, "days": 1.0, "weeks": 1.1, "months": 1.1}


@dataclass
class SectorScore:
    sub_sector: str
    score: float = 0.0
    up: float = 0.0
    down: float = 0.0
    drivers: list[tuple[float, Impact, NewsItem]] = field(default_factory=list)
    tickers: dict[str, float] = field(default_factory=dict)

    @property
    def conflict(self) -> bool:
        """Material evidence both ways: treat the net score with suspicion."""
        lo, hi = sorted((self.up, self.down))
        return hi > 0 and lo / hi > 0.5


def impact_weight(im: Impact, item: NewsItem, now: datetime, half_life_h: float) -> float:
    age_h = max(0.0, (now - item.published).total_seconds() / 3600)
    decay = 0.5 ** (age_h / half_life_h)
    corroboration = 1 + 0.25 * min(item.corroboration - 1, 3)
    sign = 1 if im.direction == "up" else -1
    return sign * im.magnitude * im.confidence * item.source_weight * decay * corroboration * HORIZON_BOOST[im.horizon]


REPEAT_DECAY = 0.5  # k-th strongest item on the same sub-sector+direction counts REPEAT_DECAY**k


def aggregate(pairs: list[tuple[Impact, NewsItem]], now: datetime, half_life_h: float = 12.0) -> list[SectorScore]:
    """Repeat coverage of one event (many outlets, different wording) must not stack linearly, so within a
    sub-sector+direction the strongest item counts fully, the next half, then a quarter, and so on."""
    groups: dict[tuple[str, int], list[tuple[float, Impact, NewsItem]]] = {}
    for im, item in pairs:
        w = impact_weight(im, item, now, half_life_h)
        groups.setdefault((im.sub_sector, 1 if w > 0 else -1), []).append((w, im, item))
    scores: dict[str, SectorScore] = {}
    for (sub, sign), rows in groups.items():
        rows.sort(key=lambda r: -abs(r[0]))
        s = scores.setdefault(sub, SectorScore(sub))
        for k, (w, im, item) in enumerate(rows):
            eff = w * REPEAT_DECAY ** k
            s.score += eff
            s.up += max(eff, 0)
            s.down += max(-eff, 0)
            s.drivers.append((w, im, item))
            for t in im.tickers:
                s.tickers[t] = s.tickers.get(t, 0.0) + eff
    for s in scores.values():
        s.drivers.sort(key=lambda d: -abs(d[0]))
    return sorted(scores.values(), key=lambda s: -abs(s.score))
