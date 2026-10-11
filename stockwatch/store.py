from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Impact, NewsItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id TEXT PRIMARY KEY, source TEXT, title TEXT, summary TEXT, url TEXT,
  published TEXT, source_weight REAL, corroboration INTEGER, analyzed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS impacts(
  item_id TEXT, sub_sector TEXT, direction TEXT, magnitude INTEGER, confidence REAL,
  horizon TEXT, rationale TEXT, tickers TEXT, precedents TEXT, engine TEXT,
  UNIQUE(item_id, sub_sector, direction));
"""


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def add_items(self, items: list[NewsItem]) -> list[NewsItem]:
        """Insert unseen items; returns only the new ones."""
        new = []
        for i in items:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO items(id,source,title,summary,url,published,source_weight,corroboration) VALUES(?,?,?,?,?,?,?,?)",
                (i.id, i.source, i.title, i.summary, i.url, i.published.isoformat(), i.source_weight, i.corroboration))
            if cur.rowcount:
                new.append(i)
        self.db.commit()
        return new

    def pending(self) -> list[NewsItem]:
        rows = self.db.execute("SELECT * FROM items WHERE analyzed=0 ORDER BY published").fetchall()
        return [self._item(r) for r in rows]

    def save_impacts(self, item_ids: list[str], impacts: list[Impact]) -> None:
        for im in impacts:
            self.db.execute(
                "INSERT OR REPLACE INTO impacts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (im.item_id, im.sub_sector, im.direction, im.magnitude, im.confidence, im.horizon,
                 im.rationale, json.dumps(im.tickers), json.dumps(im.precedents), im.engine))
        self.db.executemany("UPDATE items SET analyzed=1 WHERE id=?", [(i,) for i in item_ids])
        self.db.commit()

    def impacts_since(self, since: datetime) -> list[tuple[Impact, NewsItem]]:
        rows = self.db.execute(
            "SELECT i.*, m.* FROM impacts i JOIN items m ON m.id=i.item_id WHERE m.published>=?",
            (since.isoformat(),)).fetchall()
        out = []
        for r in rows:
            im = Impact(r["item_id"], r["sub_sector"], r["direction"], r["magnitude"], r["confidence"],
                        r["horizon"], r["rationale"], json.loads(r["tickers"]), json.loads(r["precedents"]), r["engine"])
            out.append((im, self._item(r)))
        return out

    @staticmethod
    def _item(r: sqlite3.Row) -> NewsItem:
        return NewsItem(r["id"], r["source"], r["title"], r["summary"], r["url"],
                        datetime.fromisoformat(r["published"]).astimezone(timezone.utc),
                        r["source_weight"], r["corroboration"])
