"""Kite Connect access-token helper.

Kite's access_token expires every day at ~06:00 IST. Full flow:

1. Open https://kite.zerodha.com/connect/login?v=3&api_key=<KITE_API_KEY> in a browser.
2. Log in with your Zerodha credentials.
3. You'll be redirected to <your redirect URL>?request_token=XXXX&...
4. Paste that whole redirect URL (or just the request_token) back here.

The script exchanges request_token + api_secret for an access_token and writes it to
~/.kite/access_token (chmod 600). Load it before any script that needs market data:

    export KITE_API_KEY=xxx
    export KITE_API_SECRET=yyy
    export KITE_ACCESS_TOKEN="$(cat ~/.kite/access_token)"
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def login_url(api_key: str) -> str:
    return f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"


def extract_request_token(user_input: str) -> str:
    s = user_input.strip()
    if s.startswith("http"):
        q = parse_qs(urlparse(s).query)
        if "request_token" in q:
            return q["request_token"][0]
    return s


def exchange_and_save(api_key: str, api_secret: str, request_token: str,
                      out_path: Path | None = None) -> str:
    from kiteconnect import KiteConnect  # type: ignore
    k = KiteConnect(api_key=api_key)
    data = k.generate_session(request_token, api_secret=api_secret)
    token = data["access_token"]
    out = out_path or (Path.home() / ".kite" / "access_token")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(token)
    os.chmod(out, stat.S_IRUSR | stat.S_IWUSR)
    return token


def main() -> None:
    api_key = os.environ.get("KITE_API_KEY")
    api_secret = os.environ.get("KITE_API_SECRET")
    if not (api_key and api_secret):
        print("Set KITE_API_KEY and KITE_API_SECRET in your environment first.", file=sys.stderr)
        sys.exit(2)
    print("Open this URL in your browser and log in:\n")
    print("  " + login_url(api_key) + "\n")
    print("After login you'll be redirected to a URL containing request_token=...")
    raw = input("Paste the full redirect URL (or just the request_token) here: ").strip()
    rt = extract_request_token(raw)
    try:
        token = exchange_and_save(api_key, api_secret, rt)
    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Access token saved to ~/.kite/access_token (first 8 chars: {token[:8]}...)")
    print("Export it before running the pipeline:")
    print("  export KITE_ACCESS_TOKEN=\"$(cat ~/.kite/access_token)\"")


if __name__ == "__main__":
    main()
