import argparse

from .pipeline import load_config, run_once, watch


def main() -> None:
    p = argparse.ArgumentParser(prog="stockwatch", description="Indian market news -> sector impact")
    p.add_argument("command", choices=["run", "watch"], help="run = one pass and write report; watch = loop")
    p.add_argument("--engine", choices=["auto", "llm", "rules"], default="auto")
    p.add_argument("--config")
    p.add_argument("--fixture", help="JSON file of headlines instead of live feeds (offline test)")
    a = p.parse_args()
    cfg = load_config(a.config)
    if a.command == "run":
        print(run_once(cfg, a.engine, a.fixture))
    else:
        watch(cfg, a.engine)


main()
