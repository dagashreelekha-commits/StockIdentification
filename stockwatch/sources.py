"""Fetch RSS/Atom feeds, normalise to NewsItem, de-duplicate."""
from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from .models import NewsItem

UA = "Mozilla/5.0 (compatible; stockwatch/0.1)"
_TAG = re.compile(r"<[^>]+>")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "as", "at", "by", "with", "is", "are", "after", "says"}


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", text or ""))).strip()


def _parse_date(raw: str | None, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(xml_text: str, source: str, weight: float, now: datetime) -> list[NewsItem]:
    root = ET.fromstring(xml_text)
    items: list[NewsItem] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        f: dict[str, str] = {}
        for child in el:
            name = _local(child.tag)
            if name == "link":
                f.setdefault("link", child.get("href") or (child.text or ""))
            elif name in ("title", "description", "summary", "content", "pubDate", "published", "updated"):
                f.setdefault(name, child.text or "")
        title = _clean(f.get("title"))
        if not title:
            continue
        link = (f.get("link") or "").strip()
        summary = _clean(f.get("description") or f.get("summary") or f.get("content"))
        published = _parse_date(f.get("pubDate") or f.get("published") or f.get("updated"), now)
        uid = hashlib.sha1((link or title).encode()).hexdigest()[:16]
        items.append(NewsItem(uid, source, title, summary[:600], link, published, weight))
    return items


def google_news_url(query: str) -> str:
    q = urllib.parse.quote_plus(f"{query} when:1d")
    return f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def fetch_all(cfg: dict, now: datetime | None = None) -> tuple[list[NewsItem], dict[str, str]]:
    """Returns (items, errors-by-source). A dead feed never kills the run."""
    now = now or datetime.now(timezone.utc)
    feeds = [(f["name"], f["url"], f.get("weight", 1.0)) for f in cfg.get("feeds", [])]
    feeds += [(f"GNews:{q}", google_news_url(q), cfg.get("google_news_weight", 0.7)) for q in cfg.get("google_news_queries", [])]
    items: list[NewsItem] = []
    errors: dict[str, str] = {}
    for name, url, weight in feeds:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=cfg.get("http_timeout", 15))
            r.raise_for_status()
            items += parse_feed(r.text, name, weight, now)
        except Exception as e:  # noqa: BLE001 - report and continue
            errors[name] = f"{type(e).__name__}: {e}"[:200]
    return items, errors


def _tokens(title: str) -> frozenset[str]:
    # strip " - Publisher" suffix common in Google News
    title = title.rsplit(" - ", 1)[0].lower()
    return frozenset(w for w in _WORD.findall(title) if w not in _STOP)


def dedupe(items: list[NewsItem], threshold: float = 0.6) -> list[NewsItem]:
    """Merge near-duplicate headlines (Jaccard on tokens). Keeps the highest-weight/earliest copy and
    records how many outlets carried the story: corroboration is a signal."""
    kept: list[tuple[NewsItem, frozenset[str]]] = []
    for it in sorted(items, key=lambda i: (-i.source_weight, i.published)):
        toks = _tokens(it.title)
        for k, ktoks in kept:
            union = toks | ktoks
            if union and len(toks & ktoks) / len(union) >= threshold:
                k.corroboration += 1
                break
        else:
            kept.append((it, toks))
    return [k for k, _ in kept]


def within_lookback(items: list[NewsItem], hours: float, now: datetime) -> list[NewsItem]:
    cutoff = now - timedelta(hours=hours)
    return [i for i in items if i.published >= cutoff]
