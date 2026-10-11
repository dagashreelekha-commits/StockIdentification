from __future__ import annotations

from datetime import datetime

from .aggregate import SectorScore
from .models import Knowledge

PRICED_IN = (
    "> **Read this first.** This ranks what the *news* implies. It does not know what the *price* already did. "
    "Headlines are priced within seconds to minutes; check the sector's move since the news broke before acting. "
    "Treat this as a watchlist generator, not a buy list."
)


def _bar(score: float, scale: float) -> str:
    n = min(10, round(abs(score) / scale * 10)) if scale else 0
    return ("▲" if score > 0 else "▼") * max(n, 1)


def render(scores: list[SectorScore], kb: Knowledge, now: datetime, n_items: int, errors: dict[str, str],
           engine_note: str, top: int = 6, min_score: float = 0.5) -> str:
    live = [s for s in scores if abs(s.score) >= min_score]
    bull = [s for s in live if s.score > 0][:top]
    bear = [s for s in live if s.score < 0][:top]
    scale = max([abs(s.score) for s in live] or [1.0])
    L = [f"# India market news impact: {now.strftime('%d %b %Y %H:%M UTC')}", "", PRICED_IN, "",
         f"Headlines analysed: **{n_items}** · Engine: {engine_note}", ""]

    def block(title: str, rows: list[SectorScore]):
        L.append(f"## {title}")
        if not rows:
            L.append("_Nothing above the signal threshold._\n")
            return
        for s in rows:
            info = kb.sub_sectors[s.sub_sector]
            flag = "  ⚠ **conflicting news both ways**" if s.conflict else ""
            L.append(f"### {info['name']} ({info['sector']}) {_bar(s.score, scale)} `{s.score:+.1f}`{flag}")
            top_t = sorted(s.tickers.items(), key=lambda kv: -abs(kv[1]))[:5]
            L.append("Watch: " + ", ".join(f"`{t}`" for t, _ in top_t))
            for w, im, item in s.drivers[:3]:
                prec = f" · precedent: {', '.join(im.precedents)}" if im.precedents else ""
                src = f"[{item.title}]({item.url})" if item.url else item.title
                L.append(f"- {src} _({item.source}{', ×%d outlets' % item.corroboration if item.corroboration > 1 else ''})_  \n"
                         f"  {im.direction.upper()} mag {im.magnitude} · conf {im.confidence:.2f} · {im.horizon} · {im.engine}{prec}  \n"
                         f"  {im.rationale}")
            L.append("")

    block("Bullish sub-sectors", bull)
    block("Bearish sub-sectors", bear)
    if errors:
        L.append("## Feed problems")
        L += [f"- {k}: {v}" for k, v in errors.items()]
        L.append("")
    return "\n".join(L)
