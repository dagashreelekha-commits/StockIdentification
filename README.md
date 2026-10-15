# stockwatch: Indian market news → sector/stock impact → momentum ideas

Two pipelines sharing one SQLite db and one taxonomy:

1. **News → sector impact.** Business/govt/geopolitical headlines → Claude maps to NSE sub-sectors
   (direction, magnitude, confidence, horizon, tickers), calibrated against documented precedents, ranked
   with time decay. Daily report in `reports/`.
2. **Price → momentum ideas.** Daily OHLCV for the taxonomy universe (Kite Connect primary, yfinance
   fallback) → 20-day breakout + volume + 52-week-high filter + RS vs NIFTY → combined with the news
   score to produce ranked `Idea` JSONs → backtest + gatekeeper → Telegram alert → paper trade log.

Short-term momentum ideas target *risk-adjusted* edges (Sharpe > 1.0, DD < 20%, win > 50%), not a return
headline. If a trade happens to catch a 20–30% runner, it will — but the design goal is the pipeline, not
the dream.

## Run
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...                 # news LLM; without it, rules fallback
export KITE_API_KEY=...  KITE_ACCESS_TOKEN=...      # optional; falls back to yfinance
export TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHAT_ID=... # optional; needed only for `alert`

# News pipeline (existing)
python -m stockwatch run                            # one pass -> prints + reports/YYYY-MM-DD.md
python -m stockwatch watch                          # loop every poll_seconds (config.yaml)
python -m stockwatch run --fixture tests/sample_headlines.json --engine rules   # offline demo

# Momentum pipeline (new)
python -m stockwatch fetch                          # refresh ~400 days of daily bars for the universe
python -m stockwatch scan                           # emit Idea JSONs for every breakout
python -m stockwatch backtest --held-out-days 60    # in-sample + OOS, writes reports/backtest.json
python -m stockwatch alert --dry-run                # Telegram pager (print instead of sending)
python -m stockwatch paper-open                     # open paper positions for high-confidence ideas
python -m stockwatch paper-summary                  # p&l of closed paper trades

python -m unittest discover -s tests -t .           # tests (all offline)
```

## Momentum pipeline notes
- Universe = flattened `taxonomy.yaml` (~180 liquid NSE names). Change by editing the taxonomy.
- Kite path is serialised at ~3 req/s (API limit); yfinance path is parallel (8 workers).
- Backtest uses next-day open fills, 1.5×ATR stop, 2.5×ATR target, 10-day max hold, 30 bps round-trip cost.
- Gatekeeper thresholds in `config.yaml`. Default: Sharpe ≥ 1.0, DD ≤ 20%, win ≥ 50%, t-stat ≥ 2, trades ≥ 30,
  OOS Sharpe ≥ 50% of in-sample Sharpe (overfit guard).
- Paper book lives in the same SQLite db as news; `paper-summary` shows win rate and cumulative return.
- **Not SEBI-registered research.** This is a personal research tool. If you share signals publicly in India,
  you need SEBI RA/IA registration.

## How it works
feeds → parse → drop >24h old → merge near-duplicate headlines → SQLite (never re-analyses a seen headline)
→ LLM (batches of 12, taxonomy + precedents in a cached system prompt) → strict validation (unknown sub-sector,
ticker or direction is discarded) → score = direction × magnitude × confidence × source weight × 12h half-life
decay; repeat coverage of the same event counts 1, ½, ¼… → report. If the API fails, affected headlines fall back to
`triggers.yaml` rules so nothing is silently dropped.

## Tune it (this is where the quality comes from)
- `data/taxonomy.yaml`: sub-sector → tickers. **Tickers were written from memory; verify against NSE** (renames/demergers).
- `data/precedents.yaml`: add every big India news→sector move you learn about. The LLM uses these to calibrate.
- `data/triggers.yaml`: deterministic rules, also the no-API fallback.
- `config.yaml`: feeds (URLs rot; failed feeds are listed in the report), Google News queries, thresholds.

## Known limits
- **Does not see prices.** It cannot tell you the sector already moved 4% on the news.
- Headline-only analysis (no article body). Confidence is the model's opinion, not a measured hit-rate.
- Feeds were not testable from the build sandbox (network-blocked); first live run may show dead URLs.
- No backtest yet, so the scoring weights are reasoned, not fitted.
