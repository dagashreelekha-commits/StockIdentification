from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import sources
from .aggregate import aggregate
from .analyzer import LLMEngine
from .models import DATA_DIR, Knowledge, NewsItem
from .report import render
from .rules import RulesEngine
from .store import Store


def load_config(path: str | None = None) -> dict:
    return yaml.safe_load(Path(path or Path(__file__).parent / "config.yaml").read_text())


def load_fixture(path: str) -> list[NewsItem]:
    now = datetime.now(timezone.utc)
    rows = json.loads(Path(path).read_text())
    return [NewsItem(r.get("id") or str(abs(hash(r["title"]))), r.get("source", "fixture"), r["title"],
                     r.get("summary", ""), r.get("url", ""),
                     now - timedelta(hours=r.get("age_hours", 1)), r.get("weight", 1.0)) for r in rows]


def run_once(cfg: dict, engine: str = "auto", fixture: str | None = None, now: datetime | None = None,
             store: Store | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    kb = Knowledge(DATA_DIR)
    store = store or Store(cfg["db_path"])

    if fixture:
        items, errors = load_fixture(fixture), {}
    else:
        items, errors = sources.fetch_all(cfg, now)
    items = sources.dedupe(sources.within_lookback(items, cfg["lookback_hours"], now))
    store.add_items(items)

    pending = store.pending()
    llm = LLMEngine(kb, cfg["model"], batch_size=cfg["batch_size"], min_conf=cfg["min_confidence"])
    rules = RulesEngine(kb)

    use_llm = engine in ("auto", "llm") and llm.available
    if engine == "llm" and not llm.available:
        raise SystemExit("--engine llm needs ANTHROPIC_API_KEY")

    if use_llm:
        impacts, done, failed = llm.analyze(pending)
        store.save_impacts([i.id for i in done], impacts)
        if failed and engine == "auto":  # degrade, never drop news silently
            store.save_impacts([i.id for i in failed], rules.analyze(failed))
            note = f"LLM ({cfg['model']}); {len(failed)} headlines fell back to rules after API errors"
        elif failed:
            note = f"LLM ({cfg['model']}); WARNING {len(failed)} headlines failed and will be retried next run"
        else:
            note = f"LLM ({cfg['model']}) + taxonomy + {len(kb.precedents)} precedents"
    else:
        store.save_impacts([i.id for i in pending], rules.analyze(pending))
        note = "RULES ONLY (no ANTHROPIC_API_KEY): keyword matching, will miss most nuance"

    since = now - timedelta(hours=cfg["lookback_hours"])
    scores = aggregate(store.impacts_since(since), now, cfg["half_life_hours"])
    n = len(store.db.execute("SELECT 1 FROM items WHERE published>=?", (since.isoformat(),)).fetchall())
    md = render(scores, kb, now, n, errors, note)
    out = Path(cfg["reports_dir"])
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{now.strftime('%Y-%m-%d')}.md").write_text(md)
    return md


def watch(cfg: dict, engine: str) -> None:
    while True:
        print("\033[2J\033[H" + run_once(cfg, engine), flush=True)
        time.sleep(cfg["poll_seconds"])
