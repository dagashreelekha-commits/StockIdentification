import argparse

from .pipeline import load_config, run_once, watch
from .strategy_cli import (
    cmd_alert, cmd_backtest, cmd_fetch, cmd_nightly, cmd_paper_open, cmd_paper_summary, cmd_scan,
)


def main() -> None:
    p = argparse.ArgumentParser(prog="stockwatch", description="Indian market news + momentum pipeline")
    p.add_argument("command", choices=[
        "run", "watch",                     # news pipeline (existing)
        "fetch", "scan", "backtest",        # price pipeline
        "alert", "paper-open", "paper-summary",
        "nightly",                          # fetch+scan+alert+paper-open in one go
        "kite-login",                       # refresh daily Kite access token
    ])
    p.add_argument("--engine", choices=["auto", "llm", "rules"], default="auto")
    p.add_argument("--config")
    p.add_argument("--fixture", help="JSON file of headlines instead of live feeds (offline test)")
    p.add_argument("--dry-run", action="store_true", help="pager: print instead of sending")
    p.add_argument("--idea-id", help="paper-open: open a specific idea regardless of confidence")
    p.add_argument("--days", type=int, default=400, help="fetch/backtest history window")
    p.add_argument("--held-out-days", type=int, default=60, help="backtest OOS window")
    a = p.parse_args()
    cfg = load_config(a.config)

    if a.command == "run":
        print(run_once(cfg, a.engine, a.fixture))
    elif a.command == "watch":
        watch(cfg, a.engine)
    elif a.command == "fetch":
        print(cmd_fetch(cfg, days=a.days))
    elif a.command == "scan":
        print(cmd_scan(cfg))
    elif a.command == "backtest":
        print(cmd_backtest(cfg, held_out_days=a.held_out_days))
    elif a.command == "alert":
        print(cmd_alert(cfg, dry_run=a.dry_run))
    elif a.command == "paper-open":
        print(cmd_paper_open(cfg, idea_id=a.idea_id))
    elif a.command == "paper-summary":
        print(cmd_paper_summary(cfg))
    elif a.command == "nightly":
        print(cmd_nightly(cfg, dry_run=a.dry_run))
    elif a.command == "kite-login":
        from .kite_login import main as kite_main
        kite_main()


main()
