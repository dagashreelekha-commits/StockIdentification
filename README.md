# stockwatch: Indian market news → sector/stock impact

Pulls Indian business, government (PIB, RBI, SEBI) and geopolitical news, has Claude map each headline to NSE
sub-sectors (direction, magnitude, confidence, horizon, tickers), calibrated against 19 documented past events
(`stockwatch/data/precedents.yaml`), then ranks sub-sectors with time decay and writes a daily report.

## Run
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...          # without it you get the keyword-rules engine only
python -m stockwatch run                      # one pass -> prints + reports/YYYY-MM-DD.md
python -m stockwatch watch                    # loop every poll_seconds (config.yaml)
python -m stockwatch run --fixture tests/sample_headlines.json --engine rules   # offline demo
python -m unittest discover -s tests -t .     # tests
```

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
