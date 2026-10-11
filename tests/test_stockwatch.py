import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stockwatch import sources
from stockwatch.aggregate import aggregate
from stockwatch.analyzer import parse_response
from stockwatch.models import DATA_DIR, Impact, Knowledge, NewsItem
from stockwatch.pipeline import load_config, run_once
from stockwatch.rules import RulesEngine
from stockwatch.store import Store

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
KB = Knowledge(DATA_DIR)
FIXTURE = str(Path(__file__).parent / "sample_headlines.json")

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>RBI cuts repo rate</title><link>http://x/1</link><description>&lt;p&gt;Hi &amp;amp; bye&lt;/p&gt;</description>
<pubDate>Sun, 11 Oct 2026 10:00:00 +0530</pubDate></item>
<item><title></title></item></channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Cabinet approves scheme</title>
<link href="http://x/2"/><updated>2026-10-11T08:00:00Z</updated><summary>s</summary></entry></feed>"""


def item(title, age=1.0, w=1.0, corr=1, id=None):
    return NewsItem(id or title, "t", title, "", "", NOW - timedelta(hours=age), w, corr)


class TestKnowledge(unittest.TestCase):
    def test_references_are_consistent(self):
        for p in KB.precedents:
            for s in p.get("up", []) + p.get("down", []):
                self.assertIn(s, KB.sub_sectors, f"{p['id']} -> {s}")
        for t in KB.triggers:
            for sub, d, m in t["impacts"]:
                self.assertIn(sub, KB.sub_sectors, t["id"])
                self.assertIn(d, ("up", "down"))
                self.assertTrue(1 <= m <= 5)

    def test_no_ticker_in_two_subsectors_except_known(self):
        seen = {}
        for k, v in KB.sub_sectors.items():
            for t in v["stocks"]:
                seen.setdefault(t, []).append(k)
        dupes = {t: s for t, s in seen.items() if len(s) > 1}
        self.assertEqual(set(dupes), {"DIXON"}, dupes)  # DIXON legitimately in durables + EMS


class TestSources(unittest.TestCase):
    def test_parse_rss_and_atom(self):
        r = sources.parse_feed(RSS, "s", 1.0, NOW)
        self.assertEqual(len(r), 1)  # empty-title item skipped
        self.assertEqual(r[0].summary, "Hi & bye")
        self.assertEqual(r[0].published, datetime(2026, 10, 11, 4, 30, tzinfo=timezone.utc))
        a = sources.parse_feed(ATOM, "s", 1.0, NOW)
        self.assertEqual(a[0].url, "http://x/2")
        self.assertEqual(a[0].published.hour, 8)

    def test_dedupe_merges_and_counts(self):
        items = [item("RBI cuts repo rate by 25 bps", w=1.3), item("RBI slashes repo rate by 25 bps - Mint"),
                 item("Brent crude surges on supply fears")]
        out = sources.dedupe(items)
        self.assertEqual(len(out), 2)
        self.assertEqual(max(i.corroboration for i in out), 2)

    def test_lookback(self):
        self.assertEqual(len(sources.within_lookback([item("a", 1), item("b", 30)], 24, NOW)), 1)


class TestRules(unittest.TestCase):
    def setUp(self):
        self.r = RulesEngine(KB)

    def subs(self, title):
        return {(i.sub_sector, i.direction) for i in self.r.analyze([item(title)])}

    def test_rate_cut(self):
        s = self.subs("RBI cuts repo rate by 25 bps")
        self.assertIn(("realty", "up"), s)
        self.assertNotIn(("realty", "down"), s)

    def test_crude_direction(self):
        self.assertIn(("omcs", "down"), self.subs("Brent crude surges past $95"))
        self.assertIn(("omcs", "up"), self.subs("Brent crude falls 3%"))

    def test_noise_is_ignored(self):
        self.assertEqual(self.subs("Sensex ends 150 points higher"), set())
        self.assertEqual(self.subs("5 stocks to buy today"), set())


class TestParseResponse(unittest.TestCase):
    def test_validates_and_drops_hallucinations(self):
        items = [item("a"), item("b")]
        resp = "Sure!\n```json\n" + json.dumps([
            {"i": 0, "impacts": [
                {"sub_sector": "defence", "direction": "up", "magnitude": 9, "confidence": 1.7,
                 "horizon": "someday", "rationale": "x", "tickers": ["HAL", "FAKECO"], "precedents": ["nope", "pahalgam_sindoor_2025"]},
                {"sub_sector": "made_up", "direction": "up", "magnitude": 3, "confidence": 0.9},
                {"sub_sector": "banks_psu", "direction": "sideways", "magnitude": 3, "confidence": 0.9}]},
            {"i": 7, "impacts": [{"sub_sector": "defence", "direction": "up", "magnitude": 3, "confidence": 0.9}]},
            {"i": 1, "impacts": [{"sub_sector": "telecom", "direction": "up", "magnitude": 3, "confidence": 0.1}]},
        ]) + "\n```"
        out = parse_response(resp, items, KB, min_conf=0.3)
        self.assertEqual(len(out), 1)
        im = out[0]
        self.assertEqual((im.magnitude, im.confidence, im.horizon), (5, 1.0, "days"))
        self.assertEqual(im.tickers, ["HAL"])
        self.assertEqual(im.precedents, ["pahalgam_sindoor_2025"])

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            parse_response("no json here", [item("a")], KB)


class TestAggregate(unittest.TestCase):
    def test_decay_and_conflict(self):
        def imp(d, mag=3, conf=0.8):
            return Impact("x", "omcs", d, mag, conf, "days", "r", ["IOC"])
        fresh, old = item("f", 0), item("o", 24)
        s = aggregate([(imp("down"), fresh)], NOW)[0]
        s_old = aggregate([(imp("down"), old)], NOW)[0]
        self.assertAlmostEqual(s_old.score, s.score / 4, places=6)  # 24h = 2 half-lives
        both = aggregate([(imp("down"), fresh), (imp("up"), fresh)], NOW)[0]
        self.assertTrue(both.conflict)
        self.assertAlmostEqual(both.score, 0.0)

    def test_corroboration_and_source_weight_raise_score(self):
        im = Impact("x", "omcs", "up", 3, 0.8, "days", "r")
        base = aggregate([(im, item("a"))], NOW)[0].score
        self.assertGreater(aggregate([(im, item("a", corr=3))], NOW)[0].score, base)
        self.assertGreater(aggregate([(im, item("a", w=1.3))], NOW)[0].score, base)


    def test_repeat_coverage_has_diminishing_returns(self):
        im = Impact("x", "omcs", "up", 3, 0.8, "days", "r")
        one = aggregate([(im, item("a"))], NOW)[0].score
        ten = aggregate([(im, item(f"a{n}")) for n in range(10)], NOW)[0].score
        self.assertLess(ten, one * 2.01)  # geometric series caps at 2x
        self.assertGreater(ten, one)

    def test_distinct_precedent_per_trigger(self):
        out = RulesEngine(KB).analyze([item("RBI cuts repo rate by 25 bps")])
        self.assertTrue(out and all(i.precedents == ["rbi_rate_cuts_2025"] for i in out))
        for t in KB.triggers:
            if t.get("precedent"):
                self.assertIn(t["precedent"], KB.precedent_ids)


class TestStore(unittest.TestCase):
    def test_idempotent_and_pending(self):
        st = Store(":memory:")
        a = item("a", id="1")
        self.assertEqual(len(st.add_items([a])), 1)
        self.assertEqual(len(st.add_items([a])), 0)  # already seen
        self.assertEqual(len(st.pending()), 1)
        st.save_impacts(["1"], [Impact("1", "omcs", "up", 3, 0.8, "days", "r", ["IOC"], ["crude_spike_2022"], "llm")])
        self.assertEqual(st.pending(), [])
        got = st.impacts_since(NOW - timedelta(days=1))
        self.assertEqual(got[0][0].tickers, ["IOC"])


class TestEndToEnd(unittest.TestCase):
    def test_rules_pipeline_on_fixture(self):
        import os, tempfile
        os.environ.pop("ANTHROPIC_API_KEY", None)
        cfg = load_config()
        with tempfile.TemporaryDirectory() as d:
            cfg["reports_dir"] = d
            md = run_once(cfg, engine="rules", fixture=FIXTURE, store=Store(":memory:"))
            self.assertIn("Bullish sub-sectors", md)
            self.assertIn("Defence", md)
            self.assertIn("RULES ONLY", md)
            self.assertNotIn("Sensex ends", md)  # noise headline produced no driver
            self.assertTrue(list(Path(d).glob("*.md")))


if __name__ == "__main__":
    unittest.main()
