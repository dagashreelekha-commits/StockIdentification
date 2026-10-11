"""Deterministic keyword engine. Cheap, auditable, and the fallback when the LLM is unavailable."""
from __future__ import annotations

import re

from .models import Impact, Knowledge, NewsItem

RULE_CONFIDENCE = 0.5  # regexes are blunt; never claim high confidence


class RulesEngine:
    def __init__(self, kb: Knowledge):
        self.kb = kb
        self._compiled = [(t, re.compile(t["pattern"], re.I)) for t in kb.triggers]

    def analyze(self, items: list[NewsItem]) -> list[Impact]:
        out: list[Impact] = []
        for it in items:
            text = f"{it.title}. {it.summary}"
            seen: set[tuple[str, str]] = set()
            for trig, rx in self._compiled:
                if not rx.search(text):
                    continue
                for sub, direction, mag in trig["impacts"]:
                    if (sub, direction) in seen or sub not in self.kb.sub_sectors:
                        continue
                    seen.add((sub, direction))
                    out.append(Impact(
                        it.id, sub, direction, int(mag), RULE_CONFIDENCE, "days",
                        f"[rule:{trig['id']}] {trig['why']}",
                        list(self.kb.sub_sectors[sub]["stocks"][:3]),
                        [trig["precedent"]] if trig.get("precedent") else [], "rules"))
        return out
