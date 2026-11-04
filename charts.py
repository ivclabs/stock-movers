"""
charts.py - Phase 4: add chart data to the stocks the website will show
------------------------------------------------------------------------
Reads results.json, and for every mover labelled "Solid" or "Just talk" it
downloads price history from Alpaca and saves it in two lists:

    "chart_daily"   ~12 months, one candle per trading day   (~250 candles)
    "chart_weekly"  ~2 years,   one candle per week          (~105 candles)

The website shows only the last 6 months of the daily chart. The 6 extra
months are used to calculate SMA 50, so its line starts at the chart's left edge.

How it fits together:

    movers.py ──▶ news.py ──▶ judge.py ──▶ charts.py ──▶ results.json ──▶ website
                                               │
                                               ▼
                                Alpaca historical bars (2 calls per stock)

Why only "Solid" and "Just talk"?  Those are the only stocks the website shows.
Downloading charts for the "No" stocks would just make results.json bigger.

Each candle is saved in the exact shape the chart library on the website
(TradingView Lightweight Charts) expects, so app.js can pass it straight in:
    {"time": "2026-11-03", "open": 9.1, "high": 9.6, "low": 8.9, "close": 9.4, "volume": 1234567}

Run (inside your .venv):
    python charts.py                    # add charts to results.json (after judge.py)
    python charts.py --test AAPL,NVTS   # just print a summary for some symbols
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone

# Reuse the keys, retry logic and settings we already have in movers.py
from movers import KEY_ID, SECRET, api_get, DATA_URL, DELAY, OUTPUT_FILE
from judge import SHOW_ON_SITE

# ---- Settings you can change ------------------------------------------------
DAILY_DAYS = 365         # 12 months of daily candles (website shows 6, rest feeds the SMAs)
WEEKLY_DAYS = 2 * 365    # 2 years of weekly candles


# ---- Main work ---------------------------------------------------------------
def get_bars(symbol, timeframe, days_back, now_utc):
    """Download candles for one stock. timeframe is "1Day" or "1Week"."""
    start = now_utc - timedelta(days=days_back)
    end = now_utc - DELAY            # free plan: data must be older than 15 min
    data = api_get(f"{DATA_URL}/{symbol}/bars", {
        "timeframe": timeframe,
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adjustment": "split",       # so a 10-for-1 split doesn't look like a -90 % crash
        "feed": "sip",               # whole US market (allowed because end is >15 min ago)
        "limit": 1000,               # more than enough: 2 years of weeks is ~105
    })
    candles = []
    for b in data.get("bars") or []:     # "bars" can be null for a brand-new stock
        candles.append({
            "time": b["t"][:10],         # "2026-11-03T05:00:00Z" -> "2026-11-03"
            "open": b["o"],
            "high": b["h"],
            "low": b["l"],
            "close": b["c"],
            "volume": b["v"],
        })
    return candles


def add_charts(movers, now_utc):
    """Add chart_daily + chart_weekly to the movers the website shows."""
    shown = [m for m in movers if m.get("ai", {}).get("label") in SHOW_ON_SITE]
    for i, m in enumerate(shown, 1):
        print(f"  charts {i}/{len(shown)}: {m['symbol']:<6}", end="\r", flush=True)
        m["chart_daily"] = get_bars(m["symbol"], "1Day", DAILY_DAYS, now_utc)
        m["chart_weekly"] = get_bars(m["symbol"], "1Week", WEEKLY_DAYS, now_utc)
        time.sleep(0.35)             # stay far below Alpaca's 200 calls/minute
    print()

    # A stock that was shown last run but is "No" now: remove its old charts
    for m in movers:
        if m not in shown:
            m.pop("chart_daily", None)
            m.pop("chart_weekly", None)
    return shown


def summary(symbol, daily, weekly):
    """One line per stock, e.g. to check the data looks right."""
    def span(c):
        return f"{c[0]['time']} -> {c[-1]['time']}" if c else "no data"
    return (f"  {symbol:<6} daily {len(daily):>3} candles ({span(daily)})   "
            f"weekly {len(weekly):>3} candles ({span(weekly)})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", help="comma-separated symbols, just print a summary")
    args = parser.parse_args()

    if not KEY_ID or not SECRET:
        sys.exit("Missing keys. Check .env contains APCA_KEY_ID and APCA_SECRET.")

    now_utc = datetime.now(timezone.utc)

    # Test mode: no results.json needed
    if args.test:
        for sym in args.test.upper().split(","):
            sym = sym.strip()
            daily = get_bars(sym, "1Day", DAILY_DAYS, now_utc)
            weekly = get_bars(sym, "1Week", WEEKLY_DAYS, now_utc)
            print(summary(sym, daily, weekly))
            if daily:
                print(f"         last daily candle: {daily[-1]}")
        return

    try:
        with open(OUTPUT_FILE) as f:
            result = json.load(f)
    except FileNotFoundError:
        sys.exit(f"{OUTPUT_FILE} not found. Run movers.py, news.py and judge.py first.")

    movers = result.get("movers", [])
    if any("ai" not in m for m in movers):
        sys.exit("Some movers have no AI label yet. Run judge.py first.")

    shown = [m for m in movers if m["ai"]["label"] in SHOW_ON_SITE]
    if not shown:
        print("No Solid or Just talk movers, no charts needed.")
    else:
        print(f"Fetching charts for {len(shown)} movers...")
        add_charts(movers, now_utc)

    result["charts_added_at_utc"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print("Saved.\n")
    for m in shown:
        print(summary(m["symbol"], m["chart_daily"], m["chart_weekly"]))


if __name__ == "__main__":
    main()
