"""Telegram pager. Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment.

Dedupe is a text file in reports/ -- one idea_id per line. Keeps the alert surface flat so the
same breakout doesn't ping you three days in a row.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests


def _already_sent(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}


def _remember(path: Path, ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(ids)))


def build_message(idea: dict) -> str:
    lines = [
        "STRATEGY ALERT",
        f"Strategy: {idea.get('strategy', '?')}",
        f"Symbol: {idea.get('symbol', '?')}  ({idea.get('sub_sector', '?')})",
        f"Entry hint: Rs {idea.get('entry_hint', 0):.2f}",
        f"Stop: Rs {idea.get('stop', 0):.2f}   Target: Rs {idea.get('target', 0):.2f}",
        f"ATR(14): {idea.get('atr', 0):.2f}",
        f"Momentum: {idea.get('momentum_score', 0):.2f}   News: {idea.get('news_score', 0):.2f}",
        f"Composite: {idea.get('composite', 0):.2f}",
        f"Confidence: {idea.get('confidence_pct', 0)}%",
        f"Size: {idea.get('size_pct', 0):.2f}% of equity",
        f"Reason: {idea.get('reason', '?')}",
        f"Idea id: {idea.get('idea_id', '?')}",
    ]
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 10.0) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=timeout)
        return r.ok
    except requests.RequestException:
        return False


def alert_new_ideas(ideas_dir: Path, dedupe_file: Path, min_confidence: int = 60,
                    token: str | None = None, chat_id: str | None = None, dry_run: bool = False) -> list[str]:
    """Scan ideas_dir for JSON files; send unsent ones above min_confidence. Returns ids sent."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat_id) and not dry_run:
        return []
    sent = _already_sent(dedupe_file)
    new_ids: list[str] = []
    for p in sorted(ideas_dir.glob("*.json")):
        try:
            idea = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        iid = idea.get("idea_id") or p.stem
        if iid in sent:
            continue
        if (idea.get("confidence_pct") or 0) < min_confidence:
            continue
        msg = build_message(idea)
        ok = True if dry_run else send_telegram(token, chat_id, msg)
        if ok:
            sent.add(iid)
            new_ids.append(iid)
            _remember(dedupe_file, sent)
            time.sleep(0.05)
    return new_ids
