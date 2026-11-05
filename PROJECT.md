# Stock Movers — project notes

Final project for a 10-week practice period (network technician training).
Goal: a working **website**. Pentest and QA are out of scope for this project — focus only on building the site.

## How to work with me (the student)

- I'm a visual learner: show how parts connect (small diagrams) and explain **why** a step is needed, not just the steps.
- I'm still learning Python, so keep code simple and well commented.
- I'm on Mac/Linux with bash. The project uses a virtual environment: `source .venv/bin/activate` before running anything.

## The idea

A website that shows US stocks moving up in **pre-market or after-hours**, but **only those with solid news**.
An AI decides which news is solid. Each stock shows a **12-month daily chart** and a **2-year weekly chart**.

## Filter rules (in `movers.py`)

- price ≥ $5 and ≤ $90
- no ETFs/funds: NASDAQ, NYSE, AMEX only (ARCA/BATS are almost all ETFs) + names with ETF/ETN/Fund/ProShares/Direxion/iShares/SPDR skipped
- up ≥ $0.20
- up ≥ 4 % **or** up ≥ $1 (compared with the last regular close)
- volume ≥ 100,000 shares traded in the current session

Comparison price: pre-market → previous regular close; after-hours → today's regular close.

## Data sources and decisions

| Need | Source | Notes |
|---|---|---|
| Movers (pre/after) | **Alpaca** snapshots, `feed=delayed_sip` | Free plan: whole US market, 15 min delayed. 200 calls/min. |
| Session volume | **Alpaca** 1-min bars, `feed=sip` | Allowed on free plan only if `end` is more than 15 min ago. |
| List of all stocks | **Alpaca** `paper-api.alpaca.markets/v2/assets` | Filtered to NASDAQ, NYSE, AMEX; ETFs/funds removed by name. |
| News | **Alpaca News API** (from Benzinga) | `data.alpaca.markets/v1beta1/news` — verified free on the Basic plan. |
| Charts | **Alpaca** historical bars (1Day, 1Week) | Massive free plan (2 years EOD) is the backup. |
| AI judge | **Claude API** (`claude-haiku-5-5`) | Returns `{"label": "Solid"/"Just talk"/"No", "reason": "..."}`. |

Decisions already made:
- **REST, not websockets.** Data may be up to 20 min old; we poll every 15–20 min. Free websocket is limited to 30 symbols and IEX only.
- **No scraping** of sites like Yahoo/Finviz (terms of service, breaks, gets blocked).
- Rejected: Alpha Vantage (delayed US data is premium), Alpaca's movers endpoint (only updates in regular hours), Massive free (end-of-day only, no live movers).

## Keys

Stored in `.env` (never committed, listed in `.gitignore`). Paper-trading keys from the Alpaca dashboard.

```
APCA_KEY_ID=...
APCA_SECRET=...
ANTHROPIC_API_KEY=...   # Claude API, for judge.py
```

## Planned architecture (AWS)

```
EventBridge (timer, every 20 min in pre/after sessions)
      │
      ▼
Lambda (Python): prices → filter → news → AI → chart data
      │ writes
      ▼
S3 bucket: results.json + index.html + style.css + app.js  ──▶  browser
```

No always-on server, no database: the Lambda writes `results.json`, the static website reads it.

## Phases

1. ✅ **Movers** — `movers.py` scans the whole market and saves `results.json` (built; first real run pending)
2. ✅ **News** — `news.py` adds headlines + summaries (last 24 h, 72 h on Mondays, max 10 per stock) to each mover in `results.json`
3. ✅ **AI judge** — `judge.py` (built; first real run 2026-11-04: 46 movers → 21 with news → 6 solid; after ETF + $90 filters: 12 movers → 3 Solid, 2 Just talk) sends headlines + "solid news" rules to an AI API, adds `"ai": {"label", "reason"}` to every mover. Labels: **Solid** (hard news), **Just talk** (CEO comments about the company's own business, plans, non-binding deals; comments on markets/economy = No), **No** (kept for debugging). Website shows Solid + Just talk, each with its label. Model `claude-haiku-5-5` (cheapest; ~under $1/month estimated), effort `low`, structured JSON output, answers cached in `judge_cache.json` by symbol + newest article
   - Solid: earnings beat, raised guidance, FDA approval, big contract, buyout offer
   - Just talk: CEO/management comments about the company's own business, plans, MOUs, product news without numbers
   - No: "stock is moving" articles, analyst chatter, share offerings
4. ✅ **Chart data** — `charts.py` adds `chart_daily` (~250 candles, 12 months) and `chart_weekly` (~104 candles, 2 years) to Solid + Just talk movers only. Split-adjusted, `feed=sip`, 2 Alpaca calls per stock. Candles are `{time, open, high, low, close, volume}` = Lightweight Charts format. results.json ≈ 350 KB pretty-printed (≈ 180 KB compact; consider compact/gzip in the AWS phase)
5. ✅ **Website** — `index.html` + `style.css` + `app.js` read `results.json`; one card per Solid / Just talk stock (price, change, volume, label badge, AI reason, chart with 6 months daily (12 downloaded, 6 hidden months feed the SMAs) / 2 years weekly toggle + current price as a label on the price scale (no line) + EMA 10 / EMA 20 / SMA 50 lines calculated in app.js (blue/orange/purple; on the weekly chart they count weeks), headlines with links); filter buttons All / Solid / Just talk; light + dark mode; works on phones. Lightweight Charts 4.2.0 from jsDelivr. Test locally: `python -m http.server 8000` → http://localhost:8000
5b. ❌ **System scan — removed 2026-11-05** (no tab, badge, cards, Lambda or schedule any more; `system.py` kept in the repo for reference, not used). Was: `system.py` (once a day, any time after the close) scans yesterday's daily candle of the whole market (same list as movers: no ETFs, $5–$90, volume ≥ 100k): **first green 3-bar signal after red** (high + close above the higher high of the 2 previous candles; red = mirror) **and EMA10 > SMA50, EMA20 > SMA50, EMA10 < EMA20** (EMA like thinkorswim `ExpAverage`, seeded with the first close; ~250 days of history). Writes `system.json` (separate file because movers.py rewrites results.json every 20 min). Takes ~3 min (~7,000 stocks, 100 per request). First run 2026-11-04: 3 signals on Nov 3 (SHLS, RAL, PINS). Website: **System** tab + teal badge; System charts have gray candles + 3-bar trend dots at each close (green until a sell signal, red until a green signal; calculated in app.js `trendPoints()` on all downloaded candles; drawn as 2 dot series because one mixed-colour series stops drawing after the first colour change in Lightweight Charts 4.2.0); a mover that is also a System stock gets both badges on one card
6. ✅ **AWS** (Terraform, region eu-north-1) — **deployed 2026-11-04**, live at **https://d1p6aa3eour1bj.cloudfront.net**. First runs: pipeline 44 s / 208 MB used (system 174 s / 815 MB before it was removed). http → https redirect works, bucket direct access → 403 (private). Update the site/code: `./deploy.sh`. Logs: CloudWatch `/aws/lambda/stock-movers-pipeline`. Run by hand: `aws lambda invoke --function-name stock-movers-pipeline --cli-read-timeout 900 out.json`
   - `lambda_function.py`: `pipeline_handler` (skips outside pre/post and the first 20 min of a session; downloads `judge_cache.json` from S3, runs movers → news → judge → charts, uploads `results.json` max-age=60 + cache; uploads nothing if a step fails)
   - `build_lambda.sh`: pip installs Linux x86_64 / Python 3.13 wheels + our .py files into `build/package` (14 MB zipped; tested in the official Lambda python:3.13 Docker image). `tzdata` added to requirements for zoneinfo
   - `infra/` (main.tf, variables.tf, outputs.tf): private S3 bucket + CloudFront https (OAC, CachingOptimized, gzip) + 1 Lambda (pipeline 512 MB/300 s, python3.13) + IAM roles + log group (14 days) + EventBridge Scheduler in America/New_York: pre-market `cron(0/20 4-9 ? * MON-FRI *)`, after-hours `cron(0/20 16-19 ? * MON-FRI *)`, no retries
   - Keys: Lambda env vars, passed from `.env` by `deploy.sh` as TF_VAR_… (they are stored in `infra/terraform.tfstate` → gitignored, never share it)
   - `./deploy.sh` = build + apply; `./deploy.sh destroy` = delete everything
   - Billing alarm: AWS Budget `stock-movers-monthly`, $5/month for the whole account. Emails ALERT_EMAIL (in .env) at $4 actual spend and when the forecast is over $5. Added 2026-11-04.
7. ⬜ **Polish** — ~~session label + data timestamp, "not financial advice" notice~~ (done in phase 5), ~~auto refresh~~ (app.js checks for new data every 2 min while the tab is visible, rebuilds the cards only when `generated_at_utc` changes, keeps the chosen filter; added 2026-11-04), extras if time allows

## Files

| File | What it is |
|---|---|
| `movers.py` | Phase 1 script. `python movers.py` (auto session) or `--session pre/post` to force for testing |
| `news.py` | Phase 2 script. `python news.py` (after movers.py) or `--test AAPL,NVDA` to just print news |
| `judge.py` | Phase 3 script. `python judge.py` (after news.py) or `--test AAPL,NVDA` to just print verdicts |
| `charts.py` | Phase 4 script. `python charts.py` (after judge.py) or `--test AAPL,NVTS` to just print a summary |
| `index.html`, `style.css`, `app.js` | Phase 5 website (static files, read `results.json`) |
| `system.py` | Removed System scan, kept for reference (not used by the site or AWS) |
| `lambda_function.py` | AWS Lambda entry point (pipeline) |
| `build_lambda.sh`, `deploy.sh` | Build the Lambda package / deploy or destroy with Terraform |
| `infra/` | Terraform files describing all AWS resources |
| `alpaca_premarket_test.py` | Small early test of 10 stocks (can be deleted later) |
| `requirements.txt` | `requests`, `python-dotenv`, `anthropic` |
| `.env` | API keys (secret) |
| `.gitignore` | Keeps `.env`, `.venv/`, `.vscode/` and Python junk out of Git |
| `results.json` | Output of `movers.py`, later read by the website |

## Session times (Swedish winter time)

- Pre-market: 10:00 – 15:30 (data available from ~10:20 because of the 15-min delay)
- Regular: 15:30 – 22:00
- After-hours: 22:00 – 02:00

## Notes for Phase 3 (AI judge)

- Many articles are roundups ("20 Stocks Moving Pre-Market") tagged with 10–30 tickers. Each article keeps its `symbols` list so the AI can treat these as noise.
- Analyst "Maintains Buy, lowers target" articles are common → not solid.

## Not yet verified with real data

- ~~First real run of `movers.py` during pre-market~~ ✅ 2026-11-04: 46 movers, scan takes ~7 s
- Max symbols per snapshot request (using 200)
- ~~Alpaca News API still free on the Basic plan~~ ✅ verified 2026-11-04: works with paper keys
