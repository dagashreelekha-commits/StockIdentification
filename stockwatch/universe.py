"""Trading universe: by default the tickers from the sector taxonomy. Short-term momentum needs
liquidity; the taxonomy already lists the liquid names in each sub-sector."""
from __future__ import annotations

from .models import Knowledge

# Index benchmark for relative strength. NIFTY 50 index symbol varies by data source:
# Kite uses "NIFTY 50" (space, in INDICES segment); yfinance uses "^NSEI". The broker layer
# treats this symbol as opaque -- resolve it at the call site.
BENCHMARK_KITE = "NIFTY 50"
BENCHMARK_YF = "^NSEI"


def default_universe(kb: Knowledge) -> list[str]:
    seen: list[str] = []
    for sub in kb.sub_sectors.values():
        for t in sub["stocks"]:
            if t not in seen:
                seen.append(t)
    return seen


def ticker_sector_map(kb: Knowledge) -> dict[str, str]:
    """ticker -> sub_sector id (first match wins; DIXON lands wherever it appears first)."""
    out: dict[str, str] = {}
    for sub_id, sub in kb.sub_sectors.items():
        for t in sub["stocks"]:
            out.setdefault(t, sub_id)
    return out
