"""Price data: Kite Connect primary, yfinance fallback, SQLite bar cache.

Keeps pandas out of the hot path -- bars are plain dicts. Both providers are optional at import
time so tests run without either library installed.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


@dataclass
class Bar:
    date: date  # bar date (daily); for intraday use timestamp as midnight-aligned date + separate ts field later
    open: float
    high: float
    low: float
    close: float
    volume: int


BARS_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars(
  symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER,
  PRIMARY KEY(symbol, date));
"""


class BarCache:
    """SQLite-backed cache of daily OHLCV. One row per (symbol, date)."""

    def __init__(self, db_path: str | Path):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(BARS_SCHEMA)

    def put(self, symbol: str, bars: Iterable[Bar]) -> int:
        rows = [(symbol, b.date.isoformat(), b.open, b.high, b.low, b.close, b.volume) for b in bars]
        if not rows:
            return 0
        self.db.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?)", rows)
        self.db.commit()
        return len(rows)

    def get(self, symbol: str, start: date | None = None, end: date | None = None) -> list[Bar]:
        q = "SELECT * FROM bars WHERE symbol=?"
        a: list = [symbol]
        if start:
            q += " AND date>=?"; a.append(start.isoformat())
        if end:
            q += " AND date<=?"; a.append(end.isoformat())
        q += " ORDER BY date"
        return [Bar(date.fromisoformat(r["date"]), r["open"], r["high"], r["low"], r["close"], r["volume"])
                for r in self.db.execute(q, a).fetchall()]

    def latest_date(self, symbol: str) -> date | None:
        r = self.db.execute("SELECT MAX(date) d FROM bars WHERE symbol=?", (symbol,)).fetchone()
        return date.fromisoformat(r["d"]) if r and r["d"] else None


class Broker:
    """Pluggable data source. kind in {kite, yfinance, none}."""

    def __init__(self, kind: str = "auto"):
        self.kind = self._pick(kind)
        self._kite = None
        self._instruments: dict[str, int] = {}

    @staticmethod
    def _pick(kind: str) -> str:
        if kind == "auto":
            try:
                import kiteconnect  # noqa: F401
                if os.environ.get("KITE_API_KEY") and os.environ.get("KITE_ACCESS_TOKEN"):
                    return "kite"
            except ImportError:
                pass
            try:
                import yfinance  # noqa: F401
                return "yfinance"
            except ImportError:
                return "none"
        return kind

    @property
    def available(self) -> bool:
        return self.kind in ("kite", "yfinance")

    def _ensure_kite(self):
        if self._kite is None:
            from kiteconnect import KiteConnect  # type: ignore
            k = KiteConnect(api_key=os.environ["KITE_API_KEY"])
            k.set_access_token(os.environ["KITE_ACCESS_TOKEN"])
            self._kite = k
            # Instruments dump: NSE equity only. Keep an in-memory token map.
            try:
                for row in k.instruments("NSE"):
                    if row.get("instrument_type") == "EQ" and row.get("segment") == "NSE":
                        self._instruments[row["tradingsymbol"]] = int(row["instrument_token"])
            except Exception:
                pass
        return self._kite

    def fetch(self, symbol: str, days: int = 400) -> list[Bar]:
        """Daily OHLCV ending today (UTC). Returns empty list on failure -- caller decides what to do."""
        if self.kind == "kite":
            return self._fetch_kite(symbol, days)
        if self.kind == "yfinance":
            return self._fetch_yf(symbol, days)
        return []

    def _fetch_kite(self, symbol: str, days: int) -> list[Bar]:
        try:
            k = self._ensure_kite()
            tok = self._instruments.get(symbol)
            if not tok:
                return []
            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=days)
            # Kite daily returns list of {date, open, high, low, close, volume}
            data = k.historical_data(tok, start, end, "day")
            out = []
            for r in data:
                d = r["date"]
                if hasattr(d, "date"):
                    d = d.date()
                out.append(Bar(d, float(r["open"]), float(r["high"]), float(r["low"]),
                               float(r["close"]), int(r.get("volume") or 0)))
            return out
        except Exception:
            return []

    def _fetch_yf(self, symbol: str, days: int) -> list[Bar]:
        try:
            import yfinance as yf  # type: ignore
            t = yf.Ticker(f"{symbol}.NS")
            period = f"{max(days, 30)}d" if days < 365 else f"{days // 365 + 1}y"
            h = t.history(period=period, auto_adjust=False)
            if h.empty:
                return []
            out = []
            for idx, row in h.iterrows():
                d = idx.date() if hasattr(idx, "date") else idx
                out.append(Bar(d, float(row["Open"]), float(row["High"]), float(row["Low"]),
                               float(row["Close"]), int(row["Volume"] or 0)))
            return out
        except Exception:
            return []

    def refresh(self, cache: BarCache, symbols: list[str], days: int = 400,
                sleep_between: float = 0.35, max_workers: int = 8) -> dict[str, int]:
        """Pull fresh bars for each symbol into the cache. Parallel for yfinance; serialised for Kite
        (3 req/sec hard limit on historical). Returns {symbol: rows_written}."""
        if self.kind == "kite":
            return self._refresh_serial(cache, symbols, days, sleep_between)
        return self._refresh_parallel(cache, symbols, days, max_workers)

    def _refresh_serial(self, cache: BarCache, symbols: list[str], days: int, sleep_between: float) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in symbols:
            bars = self.fetch(s, days)
            out[s] = cache.put(s, bars)
            if sleep_between:
                time.sleep(sleep_between)
        return out

    def _refresh_parallel(self, cache: BarCache, symbols: list[str], days: int, max_workers: int) -> dict[str, int]:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        out: dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(self.fetch, s, days): s for s in symbols}
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    bars = fut.result()
                except Exception:
                    bars = []
                out[s] = cache.put(s, bars)
        return out

    def ltp(self, symbol: str) -> float | None:
        """Last traded price. Kite only; None otherwise."""
        if self.kind != "kite":
            return None
        try:
            k = self._ensure_kite()
            r = k.ltp([f"NSE:{symbol}"])
            return float(r[f"NSE:{symbol}"]["last_price"])
        except Exception:
            return None
