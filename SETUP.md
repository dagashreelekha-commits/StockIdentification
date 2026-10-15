# StockIdentification — getting it running on your own machine

I cannot run this live for you from my sandbox: the environment I run in blocks all market
data hosts (Yahoo, NSE, Kite). The pipeline runs on **your** Mac or Windows PC. The whole
setup is three one-time steps, then one command every evening after market close.

If anything below breaks, copy the full error back to me and I'll fix it.

---

## One-time setup (15 minutes)

### 1. Install Python 3.10+ and clone the repo

**Mac** (Terminal):
```bash
# If you don't have python3 yet:
# brew install python
cd ~
git clone https://github.com/dagashreelekha-commits/StockIdentification.git
cd StockIdentification
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows** (PowerShell):
```powershell
cd $HOME
git clone https://github.com/dagashreelekha-commits/StockIdentification.git
cd StockIdentification
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Create a Kite Connect app (one-time, free trial, Rs 2000/month after)

1. Go to <https://developers.kite.trade/> and log in with your Zerodha ID.
2. Click **Create new app** → "Connect".
3. Give it any name. For **Redirect URL** put `http://127.0.0.1/kite` (any URL works —
   nothing will actually run there; you'll just copy the final URL from the browser).
4. After creation you get an **API Key** and **API Secret**. Keep them handy.

### 3. Create a Telegram bot (one-time, free)

1. In Telegram, search for **@BotFather** → `/newbot` → give it a name. Copy the token
   it prints (looks like `1234567890:AAE...`).
2. Message **@userinfobot** to get your numeric chat ID.
3. Send your new bot **any** message. (Telegram won't let a bot message you first until
   you message it once.)

### 4. Set environment variables (so you don't paste keys every time)

**Mac** — add to `~/.zshrc` (or `~/.bash_profile`):
```bash
export KITE_API_KEY="paste_your_kite_api_key"
export KITE_API_SECRET="paste_your_kite_api_secret"
export TELEGRAM_BOT_TOKEN="paste_your_telegram_token"
export TELEGRAM_CHAT_ID="paste_your_chat_id"
export ANTHROPIC_API_KEY="paste_your_anthropic_key"   # optional, improves news scoring
```
Then run `source ~/.zshrc`.

**Windows** — run once in PowerShell, then restart PowerShell:
```powershell
[Environment]::SetEnvironmentVariable("KITE_API_KEY", "paste...", "User")
[Environment]::SetEnvironmentVariable("KITE_API_SECRET", "paste...", "User")
[Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "paste...", "User")
[Environment]::SetEnvironmentVariable("TELEGRAM_CHAT_ID", "paste...", "User")
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "paste...", "User")
```

---

## Every day (30 seconds)

Kite's access token expires every day around 6 AM IST. Refresh it once per day:

```bash
# From inside the repo, with .venv activated:
python -m stockwatch kite-login
```

It will:
1. Print a URL. Open it in your browser.
2. Log in to Zerodha. You'll be redirected to a URL that starts with `http://127.0.0.1/kite?...`.
3. **Copy that whole redirect URL** and paste it back in the terminal.
4. The script saves `~/.kite/access_token` with the day's token.

Then export it (once per day):
```bash
export KITE_ACCESS_TOKEN="$(cat ~/.kite/access_token)"
```

(On Windows: `$env:KITE_ACCESS_TOKEN = Get-Content $HOME\.kite\access_token`)

---

## Run the whole pipeline — one command

After market close (~ 4 PM IST on a trading day):

```bash
python -m stockwatch nightly
```

This chains:

1. **fetch** — pulls today's bars for ~180 NSE tickers into the local SQLite cache (~2–3 min on Kite).
2. **scan** — writes an `Idea JSON` for every breakout to `reports/ideas/`.
3. **alert** — Telegram pings for new ideas above the confidence threshold (`alert_min_confidence` in `config.yaml`, default 60).
4. **paper-open** — opens paper positions above the paper threshold (default 65).
5. **paper-summary** — prints the running paper-book win rate and p&l.

The first alert should hit your phone within a minute.

---

## First-time: validate before you trust it

Before you let it alert you, run a backtest on the data you just fetched:

```bash
python -m stockwatch backtest --held-out-days 60
```

You'll see something like:
```
In-sample Sharpe 1.42 (87 trades), OOS Sharpe 1.11 (14 trades),
DD 11.3%, win 56.1% -> APPROVED
```

If it says **REJECTED**, don't trade this version live. The reasons will tell you whether
it's noise (relax `max_dist_52w` in config) or genuine overfit (shorten held-out, or wait
for more data).

---

## Troubleshooting

| Symptom                                    | Fix                                                                                 |
|--------------------------------------------|-------------------------------------------------------------------------------------|
| `No module named 'kiteconnect'`            | `pip install -r requirements.txt` inside the venv                                   |
| `kite-login` fails with "invalid token"    | request_token is single-use — rerun kite-login, don't reuse the URL                 |
| `fetch` prints "fetched 0/180 symbols"     | KITE_ACCESS_TOKEN not exported, or expired — rerun kite-login                       |
| No Telegram alerts arrive                  | Did you send your bot a message once? Check `reports/alerts_sent.txt` for dedupe    |
| `scan` says "0 breakouts"                  | Normal on quiet days. Try `min_vol_ratio: 1.3` in config.yaml                       |
| Backtest `REJECTED: too few trades`        | Fetch more history: `python -m stockwatch fetch --days 1000`                        |

---

## Scheduling it daily (Mac)

```bash
# After `python -m stockwatch kite-login`, nightly can run via launchd/cron.
# Save as ~/Library/LaunchAgents/com.stockwatch.nightly.plist and `launchctl load` it.
```
Easiest: just set a daily reminder on your phone to run `python -m stockwatch kite-login`
and then `python -m stockwatch nightly` after market close. No cron needed for one command.

---

## A warning I keep repeating

- **Paper trade for 2–4 weeks** before any real money.
- **Position size** is 1% of equity max — the system is designed to lose small on bad trades.
- No signals go in a public group (SEBI RA registration required to publish research in India).
