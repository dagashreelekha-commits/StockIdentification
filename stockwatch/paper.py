"""Paper trade log.

Open a position when an idea is approved; close it when stop/target/time hits or when the user
closes it manually. Stored in the main stockwatch SQLite db.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades(
  idea_id TEXT PRIMARY KEY,
  symbol TEXT, strategy TEXT,
  opened_at TEXT, entry REAL, stop REAL, target REAL, atr REAL, size_pct REAL,
  closed_at TEXT, exit_price REAL, exit_reason TEXT, ret_net REAL,
  notes TEXT);
"""


@dataclass
class PaperTrade:
    idea_id: str
    symbol: str
    strategy: str
    entry: float
    stop: float
    target: float
    size_pct: float


class PaperBook:
    def __init__(self, db_path: str | Path):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(PAPER_SCHEMA)

    def open(self, idea: dict) -> bool:
        """Returns True if a new row was inserted, False if the idea_id was already there."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self.db.execute(
            "INSERT OR IGNORE INTO paper_trades("
            "idea_id, symbol, strategy, opened_at, entry, stop, target, atr, size_pct"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (idea["idea_id"], idea["symbol"], idea.get("strategy", ""), now,
             idea["entry_hint"], idea["stop"], idea["target"], idea.get("atr", 0.0), idea.get("size_pct", 0.0)))
        self.db.commit()
        return bool(cur.rowcount)

    def mark_closed(self, idea_id: str, exit_price: float, exit_reason: str,
                    closed_at: datetime | None = None) -> None:
        row = self.db.execute("SELECT entry FROM paper_trades WHERE idea_id=? AND closed_at IS NULL",
                              (idea_id,)).fetchone()
        if not row:
            return
        entry = row["entry"]
        ret = (exit_price - entry) / entry - 0.003  # 30bps round-trip
        self.db.execute(
            "UPDATE paper_trades SET closed_at=?, exit_price=?, exit_reason=?, ret_net=? WHERE idea_id=?",
            ((closed_at or datetime.now(timezone.utc)).isoformat(), exit_price, exit_reason, ret, idea_id))
        self.db.commit()

    def open_positions(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM paper_trades WHERE closed_at IS NULL ORDER BY opened_at").fetchall()]

    def summary(self) -> dict:
        rows = self.db.execute("SELECT * FROM paper_trades WHERE closed_at IS NOT NULL").fetchall()
        if not rows:
            return {"n": 0, "win_rate": 0.0, "avg_ret": 0.0, "total_ret": 0.0}
        rets = [r["ret_net"] for r in rows]
        wins = sum(1 for r in rets if r > 0)
        total = 1.0
        for r in rets:
            total *= 1 + r
        return {"n": len(rets), "win_rate": wins / len(rets),
                "avg_ret": sum(rets) / len(rets), "total_ret": total - 1}
