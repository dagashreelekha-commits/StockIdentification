from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).parent / "data"


@dataclass
class NewsItem:
    id: str
    source: str
    title: str
    summary: str
    url: str
    published: datetime  # timezone-aware UTC
    source_weight: float = 1.0
    corroboration: int = 1  # how many near-duplicate headlines were merged into this one


@dataclass
class Impact:
    item_id: str
    sub_sector: str
    direction: str  # "up" | "down"
    magnitude: int  # 1-5
    confidence: float  # 0-1
    horizon: str  # "intraday" | "days" | "weeks" | "months"
    rationale: str
    tickers: list[str] = field(default_factory=list)
    precedents: list[str] = field(default_factory=list)
    engine: str = "llm"  # "llm" | "rules"


class Knowledge:
    """Taxonomy + precedents + triggers, loaded once."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.sub_sectors: dict = yaml.safe_load((data_dir / "taxonomy.yaml").read_text())["sub_sectors"]
        self.precedents: list[dict] = yaml.safe_load((data_dir / "precedents.yaml").read_text())["precedents"]
        self.triggers: list[dict] = yaml.safe_load((data_dir / "triggers.yaml").read_text())["triggers"]
        self.precedent_ids = {p["id"] for p in self.precedents}

    def valid_tickers(self, sub_sector: str) -> set[str]:
        return set(self.sub_sectors[sub_sector]["stocks"])

    def name(self, sub_sector: str) -> str:
        return self.sub_sectors[sub_sector]["name"]
