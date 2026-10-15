"""Tests for the price-aware momentum pipeline. All offline: no yfinance/Kite calls."""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from stockwatch.backtest import run_backtest
from stockwatch.broker import Bar, BarCache
from stockwatch.gatekeeper import Thresholds, approve
from stockwatch.ideas import build_ideas
from stockwatch.models import DATA_DIR, Knowledge
from stockwatch.momentum import analyze_one, scan
from stockwatch.pager import build_message
from stockwatch.paper import PaperBook

KB = Knowledge(DATA_DIR)


def make_bars(prices, start=date(2024, 1, 1), vol=100_000):
    """Daily bars from a price list. OHLC = (close-0.5, close+1, close-1, close), volume = vol."""
    out = []
    d = start
    for p in prices:
        out.append(Bar(d, p - 0.5, p + 1, p - 1, p, vol))
        # Skip weekends to keep it realistic-looking.
        d = d + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return out


class TestMomentum(unittest.TestCase):
    def test_no_signal_without_breakout(self):
        # Flat prices, no breakout.
        bars = make_bars([100] * 80)
        sig = analyze_one("X", bars, bench_30d_ret=0.0)
        self.assertIsNone(sig)

    def test_breakout_with_volume_fires(self):
        # 70 bars at 100, then today pushes to 110 on 3x volume.
        bars = make_bars([100] * 70, vol=100_000)
        # Append a breakout bar.
        last = bars[-1]
        bars.append(Bar(last.date + timedelta(days=1), 101, 112, 100.5, 110, 300_000))
        sig = analyze_one("X", bars, bench_30d_ret=0.0)
        self.assertIsNotNone(sig)
        self.assertGreater(sig.volume_ratio, 2.5)
        self.assertGreater(sig.score, 0.5)

    def test_breakout_but_weak_volume_filtered(self):
        bars = make_bars([100] * 70)
        last = bars[-1]
        bars.append(Bar(last.date + timedelta(days=1), 101, 112, 100.5, 110, 110_000))
        sig = analyze_one("X", bars, bench_30d_ret=0.0, min_vol_ratio=1.5)
        self.assertIsNone(sig)

    def test_scan_uses_cache(self):
        cache = BarCache(":memory:")
        bars = make_bars([100] * 70) + [Bar(date(2024, 4, 15), 101, 112, 100.5, 110, 300_000)]
        cache.put("RELIANCE", bars)
        sigs = scan(cache, ["RELIANCE"], benchmark_bars=[], min_vol_ratio=1.5)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].symbol, "RELIANCE")


class TestBarCache(unittest.TestCase):
    def test_roundtrip(self):
        c = BarCache(":memory:")
        bars = make_bars([100, 101, 102])
        self.assertEqual(c.put("X", bars), 3)
        back = c.get("X")
        self.assertEqual(len(back), 3)
        self.assertEqual(back[-1].close, 102)
        self.assertEqual(c.latest_date("X"), bars[-1].date)

    def test_replace_keeps_latest(self):
        c = BarCache(":memory:")
        d = date(2024, 1, 1)
        c.put("X", [Bar(d, 1, 1, 1, 1, 100)])
        c.put("X", [Bar(d, 9, 9, 9, 9, 999)])  # same key overwrites
        self.assertEqual(c.get("X")[0].close, 9)


class TestIdeas(unittest.TestCase):
    def test_build_and_write(self):
        cache = BarCache(":memory:")
        bars = make_bars([100] * 70) + [Bar(date(2024, 4, 15), 101, 112, 100.5, 110, 300_000)]
        cache.put("HDFCBANK", bars)
        sigs = scan(cache, ["HDFCBANK"], benchmark_bars=[])
        ideas = build_ideas(sigs, sector_scores=[], kb=KB)
        self.assertEqual(len(ideas), 1)
        i = ideas[0]
        self.assertEqual(i.symbol, "HDFCBANK")
        self.assertEqual(i.sub_sector, "banks_private")
        self.assertGreater(i.target, i.entry_hint)
        self.assertLess(i.stop, i.entry_hint)
        # JSON round-trips cleanly.
        d = json.loads(i.to_json())
        self.assertEqual(d["strategy"], "momentum_breakout")


class TestBacktest(unittest.TestCase):
    def test_runs_on_cached_symbol(self):
        cache = BarCache(":memory:")
        # Construct a long series with a few breakouts so the backtester finds trades.
        prices = []
        for cycle in range(6):
            prices += [100 + cycle * 2] * 30            # base
            prices += list(range(100 + cycle * 2, 100 + cycle * 2 + 15))  # rally
        bars = make_bars(prices, vol=120_000)
        # Make rally bars high-volume so breakouts trigger.
        for i, b in enumerate(bars):
            if i > 30 and bars[i].close > bars[i - 1].close:
                bars[i] = Bar(b.date, b.open, b.high, b.low, b.close, 300_000)
        cache.put("TESTCO", bars)
        is_, oos = run_backtest(cache, ["TESTCO"], benchmark_bars=[], held_out_days=30)
        # Smoke test: backtester runs end-to-end on cached data and returns well-formed results.
        self.assertGreaterEqual(is_.n_trades, 0)
        self.assertGreaterEqual(oos.n_trades, 0)
        self.assertIsInstance(is_.to_dict(), dict)


class TestGatekeeper(unittest.TestCase):
    def test_rejects_thin_sample(self):
        from stockwatch.backtest import BacktestResult
        is_ = BacktestResult("is", sharpe=2.0, max_drawdown=0.1, win_rate=0.6, t_stat=3.0, n_trades=5)
        oos = BacktestResult("oos")
        ok, reasons = approve(is_, oos)
        self.assertFalse(ok)
        self.assertTrue(any("too few" in r for r in reasons))

    def test_approves_strong_result(self):
        from stockwatch.backtest import BacktestResult
        is_ = BacktestResult("is", sharpe=1.5, max_drawdown=0.1, win_rate=0.55, t_stat=2.5, n_trades=50)
        oos = BacktestResult("oos", sharpe=1.0, max_drawdown=0.08, win_rate=0.55, t_stat=1.8, n_trades=12)
        ok, reasons = approve(is_, oos)
        self.assertTrue(ok, reasons)

    def test_catches_oos_collapse(self):
        from stockwatch.backtest import BacktestResult
        is_ = BacktestResult("is", sharpe=2.0, max_drawdown=0.1, win_rate=0.6, t_stat=3.0, n_trades=50)
        oos = BacktestResult("oos", sharpe=0.3, max_drawdown=0.1, win_rate=0.5, t_stat=1.0, n_trades=12)
        ok, reasons = approve(is_, oos)
        self.assertFalse(ok)
        self.assertTrue(any("OOS Sharpe" in r for r in reasons))


class TestPager(unittest.TestCase):
    def test_message_has_all_fields(self):
        idea = {"idea_id": "MB-X-2024", "strategy": "momentum_breakout", "symbol": "RELIANCE",
                "sub_sector": "reliance", "entry_hint": 2900.5, "stop": 2850.0, "target": 2990.0,
                "atr": 30.0, "momentum_score": 0.72, "news_score": 0.5, "composite": 0.77,
                "confidence_pct": 77, "size_pct": 0.25, "reason": "breakout + vol"}
        m = build_message(idea)
        self.assertIn("RELIANCE", m)
        self.assertIn("77%", m)
        self.assertIn("Target: Rs 2990.00", m)


class TestPaper(unittest.TestCase):
    def test_open_and_close(self):
        pb = PaperBook(":memory:")
        idea = {"idea_id": "MB-X-1", "symbol": "X", "strategy": "mb", "entry_hint": 100.0,
                "stop": 95.0, "target": 110.0, "atr": 3.0, "size_pct": 0.5}
        self.assertTrue(pb.open(idea))
        self.assertFalse(pb.open(idea))              # idempotent
        self.assertEqual(len(pb.open_positions()), 1)
        pb.mark_closed("MB-X-1", 108.0, "target")
        s = pb.summary()
        self.assertEqual(s["n"], 1)
        self.assertEqual(s["win_rate"], 1.0)
        self.assertAlmostEqual(s["avg_ret"], 0.08 - 0.003, places=4)
        self.assertEqual(len(pb.open_positions()), 0)


class TestCLISmoke(unittest.TestCase):
    """End-to-end wiring: fetch is skipped (no network), but scan/backtest/alert work on seeded cache."""

    def test_scan_and_alert_offline(self):
        from stockwatch.strategy_cli import cmd_alert, cmd_scan
        from stockwatch.broker import BarCache
        with tempfile.TemporaryDirectory() as d:
            db = f"{d}/sw.db"
            cfg = {"db_path": db, "reports_dir": d, "ideas_dir": f"{d}/ideas",
                   "lookback_hours": 24, "half_life_hours": 12, "alert_min_confidence": 50}
            cache = BarCache(db)
            bars = make_bars([100] * 70) + [Bar(date(2024, 4, 15), 101, 112, 100.5, 110, 300_000)]
            cache.put("HDFCBANK", bars)
            # Scan writes ideas; alert sees them in dry-run.
            msg = cmd_scan(cfg)
            self.assertIn("ideas written", msg)
            self.assertTrue(list(Path(f"{d}/ideas").glob("*.json")))
            alert_msg = cmd_alert(cfg, dry_run=True)
            self.assertIn("Sent", alert_msg)


if __name__ == "__main__":
    unittest.main()
