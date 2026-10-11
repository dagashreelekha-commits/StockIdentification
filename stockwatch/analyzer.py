"""LLM engine: Claude reads headlines in batches and maps them to sub-sectors using our taxonomy
and historical precedents. Output is strictly validated; anything outside the taxonomy is dropped."""
from __future__ import annotations

import json
import os
import re

import requests

from .models import Impact, Knowledge, NewsItem

API_URL = "https://api.anthropic.com/v1/messages"
HORIZONS = {"intraday", "days", "weeks", "months"}


def build_system_prompt(kb: Knowledge) -> str:
    sectors = "\n".join(f"- {k}: {v['name']} [{', '.join(v['stocks'])}]" for k, v in kb.sub_sectors.items())
    prec = "\n".join(
        f"- {p['id']} ({p['date']}, {p['confidence']}): {p['event']} -> "
        f"up={p.get('up', [])} down={p.get('down', [])}. {p.get('note', '')}" for p in kb.precedents)
    return f"""You are a senior Indian equity strategist. You read news headlines and decide which NSE sub-sectors
and stocks are likely to be affected, in which direction, and how strongly.

SUB-SECTORS (id: name [stocks]). Use ONLY these ids and ONLY tickers listed under the chosen sub-sector:
{sectors}

HISTORICAL PRECEDENTS (use to calibrate; cite ids in "precedents" when a headline resembles one):
{prec}

RULES
- Most headlines are noise. Return an empty impacts list for them. Do NOT force a sector link.
- Only score what the news changes about future earnings, costs, demand, regulation or flows. Opinion pieces,
  stock-tip listicles, routine price-move recaps ("Sensex ends higher") and already-known events get no impacts.
- magnitude 1-5: 1 = marginal, 3 = sector-wide earnings relevance, 5 = regime change (e.g. 2019 corporate tax cut).
- confidence 0-1: how sure you are about direction AND relevance given only the headline. Headlines that are
  ambiguous, speculative ("may", "likely to consider") or unconfirmed rumours must be <= 0.4.
- horizon: intraday | days | weeks | months (how long the effect plays out).
- Name second-order effects (e.g. crude up -> OMCs/paints/aviation down, upstream oil up) not just the obvious name.
- Policy/government/regulator and geopolitical headlines matter as much as corporate news.
- Single-company news (results, orders): map to that company's sub-sector with only that ticker, magnitude <= 2
  unless it is a bellwether that moves the whole sub-sector.

OUTPUT: a single JSON array, no prose, one element per input headline, in order:
[{{"i": <input index>, "impacts": [{{"sub_sector": "<id>", "direction": "up|down", "magnitude": 1-5,
"confidence": 0.0-1.0, "horizon": "intraday|days|weeks|months", "rationale": "<=25 words",
"tickers": ["..."], "precedents": ["..."]}}]}}]"""


def _format_batch(items: list[NewsItem]) -> str:
    return "\n".join(f"[{n}] ({i.source}) {i.title} — {i.summary[:300]}" for n, i in enumerate(items))


def parse_response(text: str, items: list[NewsItem], kb: Knowledge, min_conf: float = 0.0) -> list[Impact]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON array in model output")
    rows = json.loads(m.group(0))
    out: list[Impact] = []
    for row in rows:
        idx = row.get("i")
        if not isinstance(idx, int) or not 0 <= idx < len(items):
            continue
        for im in row.get("impacts", []):
            sub = im.get("sub_sector")
            direction = im.get("direction")
            if sub not in kb.sub_sectors or direction not in ("up", "down"):
                continue
            try:
                mag = max(1, min(5, int(im.get("magnitude", 1))))
                conf = max(0.0, min(1.0, float(im.get("confidence", 0))))
            except (TypeError, ValueError):
                continue
            if conf < min_conf:
                continue
            horizon = im.get("horizon") if im.get("horizon") in HORIZONS else "days"
            valid = kb.valid_tickers(sub)
            tickers = [t for t in im.get("tickers", []) if t in valid] or list(kb.sub_sectors[sub]["stocks"][:3])
            prec = [p for p in im.get("precedents", []) if p in kb.precedent_ids]
            out.append(Impact(items[idx].id, sub, direction, mag, conf, horizon,
                              str(im.get("rationale", ""))[:200], tickers, prec, "llm"))
    return out


class LLMEngine:
    def __init__(self, kb: Knowledge, model: str, api_key: str | None = None, batch_size: int = 12, min_conf: float = 0.0):
        self.kb, self.model, self.batch_size, self.min_conf = kb, model, batch_size, min_conf
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._system = build_system_prompt(kb)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _call(self, items: list[NewsItem]) -> str:
        r = requests.post(
            API_URL,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": self.model,
                "max_tokens": 4096,
                "system": [{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": _format_batch(items)}],
            },
            timeout=120)
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")

    def analyze(self, items: list[NewsItem]) -> tuple[list[Impact], list[NewsItem], list[NewsItem]]:
        """Returns (impacts, analyzed_items, failed_items). Failed batches are left for the caller to retry/fallback."""
        impacts: list[Impact] = []
        done: list[NewsItem] = []
        failed: list[NewsItem] = []
        for s in range(0, len(items), self.batch_size):
            batch = items[s:s + self.batch_size]
            try:
                impacts += parse_response(self._call(batch), batch, self.kb, self.min_conf)
                done += batch
            except Exception:  # noqa: BLE001 - network/parse failure: hand batch back for fallback
                failed += batch
        return impacts, done, failed
