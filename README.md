# Stock Movers

**Live site: https://d1p6aa3eour1bj.cloudfront.net**

A website that shows US stocks moving **up** in pre-market and after-hours trading,
but only the ones with real news behind the move. An AI (Claude Haiku) reads the
headlines and labels each stock. Final project of a 10-week network technician course.

| Label | Meaning |
|---|---|
| **Solid** | Concrete news about the company: earnings, contract, FDA approval, buyout... |
| **Just talk** | Positive words about the company's own business, without hard facts |

Stocks the AI labels **No** (no news, unclear or unrelated news) are not shown.

## How it works

```
Alpaca API ──▶ movers.py ──▶ news.py ──▶ judge.py ──▶ charts.py ──▶ results.json
 (prices,      stocks up     headlines    Claude AI     daily +            │
  news)        $5–$90, no    for each     labels the    weekly candles     ▼
               ETFs          mover        news                      website (index.html,
                                                                     app.js, style.css)
```

In AWS (built with Terraform, folder `infra/`):

```
EventBridge Scheduler ──▶ Lambda "pipeline"  every 20 min in pre-market / after-hours
                                 │ results.json
                                 ▼
                       S3 bucket (private) ──▶ CloudFront (https) ──▶ visitors
```

A $5/month AWS Budget emails a warning if costs ever go up.

## Files

| File | What it does |
|---|---|
| `movers.py` | Finds stocks up in pre-market / after-hours (price $5–$90, no ETFs) |
| `news.py` | Gets the latest headlines for each mover |
| `judge.py` | Asks Claude Haiku to label the news: Solid / Just talk / No |
| `charts.py` | Downloads daily and weekly candles for the charts |
| `system.py` | An earlier extra (nightly 3-bar trend scan), no longer used by the site |
| `index.html`, `style.css`, `app.js` | The website (charts: TradingView Lightweight Charts) |
| `lambda_function.py` | Runs the scripts inside AWS Lambda |
| `infra/` | Terraform: everything in AWS |
| `build_lambda.sh`, `deploy.sh` | Build the Lambda package and deploy |
| `PROJECT.md` | Project notes, phase by phase |

## Run it on your own computer

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then put your own keys in .env
python movers.py && python news.py && python judge.py && python charts.py
python -m http.server 8000    # open http://localhost:8000
```

Keys needed: a free [Alpaca](https://alpaca.markets) account and a Claude API key.

*Not financial advice.*
