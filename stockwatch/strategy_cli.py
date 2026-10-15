"""Entry points for the price-aware pipeline. Separate module so __main__ stays small."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import ideas as ideas_mod
from .aggregate import aggregate
from .backtest import run_backtest
from .broker import BarCache, Broker
from .gatekeeper import Thresholds, approve
from .models import DATA_DIR, Knowledge
from .momentum import scan
from .pager import alert_new_ideas
from .paper import PaperBook
from .pipeline import load_config
from .store import Store
from .universe import BENCHMARK_KITE, BENCHMARK_YF, default_universe


def _benchmark_bars(broker: Broker, cache: BarCache, days: int) -> list:
    sym = BENCHMARK_KITE if broker.kind == "kite" else BENCHMARK_YF
    bars = broker.fetch(sym, days) if broker.available else []
    if bars:
        cache.put(sym, bars)
    return bars or cache.get(sym)


def cmd_fetch(cfg: dict, days: int = 400) -> str:
    """Refresh daily bars for the whole universe into the SQLite cache."""
    kb = Knowledge(DATA_DIR)
    broker = Broker()
    cache = BarCache(cfg["db_path"])
    if not broker.available:
        return "No broker available (install kiteconnect+creds or yfinance)."
    universe = default_universe(kb)
    counts = broker.refresh(cache, universe, days=days)
    _benchmark_bars(broker, cache, days)
    filled = sum(1 for v in counts.values() if v > 0)
    return f"[{broker.kind}] fetched {filled}/{len(universe)} symbols, benchmark cached."


def cmd_scan(cfg: dict, min_vol_ratio: float = 1.5, max_dist_52w: float = 0.15) -> str:
    """Scan the cached bars; write Idea JSONs for every breakout; return a one-screen summary."""
    kb = Knowledge(DATA_DIR)
    broker = Broker()
    cache = BarCache(cfg["db_path"])
    universe = default_universe(kb)
    bench = _benchmark_bars(broker, cache, 60)
    signals = scan(cache, universe, bench, min_vol_ratio=min_vol_ratio, max_dist_52w=max_dist_52w)

    # Pull existing news-based sector scores from the stockwatch DB.
    store = Store(cfg["db_path"])
    since = datetime.now(timezone.utc) - timedelta(hours=cfg["lookback_hours"])
    sector_scores = aggregate(store.impacts_since(since), datetime.now(timezone.utc), cfg["half_life_hours"])

    ideas = ideas_mod.build_ideas(signals, sector_scores, kb)
    out_dir = Path(cfg.get("ideas_dir", "reports/ideas"))
    ideas_mod.write_ideas(ideas, out_dir)

    if not ideas:
        return f"Scan: 0 breakouts in universe of {len(universe)}."
    lines = [f"Scan: {len(ideas)} ideas written to {out_dir}", "", "Top 10:"]
    for i in ideas[:10]:
        lines.append(f"  {i.symbol:<12} comp={i.composite:.2f}  mom={i.momentum_score:.2f}  "
                     f"news={i.news_score:.2f}  entry={i.entry_hint:.2f} stop={i.stop:.2f} tgt={i.target:.2f}")
    return "\n".join(lines)


def cmd_backtest(cfg: dict, held_out_days: int = 60) -> str:
    kb = Knowledge(DATA_DIR)
    broker = Broker()
    cache = BarCache(cfg["db_path"])
    universe = default_universe(kb)
    bench = _benchmark_bars(broker, cache, 400)
    is_, oos = run_backtest(cache, universe, bench, held_out_days=held_out_days)
    th = Thresholds(**cfg.get("gatekeeper", {}))
    ok, reasons = approve(is_, oos, th)
    out = {"in_sample": is_.to_dict(), "out_of_sample": oos.to_dict(),
           "approved": ok, "reject_reasons": reasons,
           "thresholds": {k: getattr(th, k) for k in th.__dataclass_fields__}}
    Path(cfg.get("reports_dir", "reports")).mkdir(parents=True, exist_ok=True)
    report = Path(cfg.get("reports_dir", "reports")) / "backtest.json"
    report.write_text(json.dumps(out, indent=2))
    verdict = "APPROVED" if ok else "REJECTED: " + "; ".join(reasons)
    return (f"Backtest: in-sample Sharpe {is_.sharpe:.2f} ({is_.n_trades} trades), "
            f"OOS Sharpe {oos.sharpe:.2f} ({oos.n_trades} trades), DD {is_.max_drawdown:.1%}, "
            f"win {is_.win_rate:.1%} -> {verdict}\nFull report: {report}")


def cmd_alert(cfg: dict, dry_run: bool = False) -> str:
    ideas_dir = Path(cfg.get("ideas_dir", "reports/ideas"))
    dedupe = Path(cfg.get("reports_dir", "reports")) / "alerts_sent.txt"
    min_conf = int(cfg.get("alert_min_confidence", 60))
    sent = alert_new_ideas(ideas_dir, dedupe, min_confidence=min_conf, dry_run=dry_run)
    tag = "[dry-run]" if dry_run else ""
    return f"{tag} Sent {len(sent)} alerts: {sent}" if sent else f"{tag} No new alerts."


def cmd_paper_open(cfg: dict, idea_id: str | None = None) -> str:
    """Open paper trades for every idea above the confidence bar (or a specific one)."""
    ideas_dir = Path(cfg.get("ideas_dir", "reports/ideas"))
    book = PaperBook(cfg["db_path"])
    min_conf = int(cfg.get("paper_min_confidence", cfg.get("alert_min_confidence", 60)))
    opened = []
    for p in sorted(ideas_dir.glob("*.json")):
        idea = json.loads(p.read_text())
        if idea_id and idea["idea_id"] != idea_id:
            continue
        if (idea.get("confidence_pct") or 0) < min_conf and not idea_id:
            continue
        if book.open(idea):
            opened.append(idea["idea_id"])
    return f"Opened {len(opened)} paper positions: {opened}" if opened else "No new paper positions to open."


def cmd_paper_summary(cfg: dict) -> str:
    book = PaperBook(cfg["db_path"])
    s = book.summary()
    open_pos = book.open_positions()
    return (f"Paper book: {s['n']} closed, win rate {s['win_rate']:.1%}, "
            f"avg {s['avg_ret']:.2%}, cumulative {s['total_ret']:.2%}; "
            f"{len(open_pos)} open positions.")
