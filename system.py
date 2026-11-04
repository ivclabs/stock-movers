"""
system.py - the "System" scan: first green signal after red + moving-average filter
-------------------------------------------------------------------------------------
Looks at YESTERDAY's daily candle of every US stock (no live data needed) and
keeps the stocks where your thinkorswim scan would fire:

  1. Green signal:  high AND close above the higher high of the 2 previous candles
     Red signal:    low  AND close below the lower  low  of the 2 previous candles
  2. First green after red:  green signal yesterday, and the last signal before
     it was red
  3. MA filter:  EMA10 > SMA50  and  EMA20 > SMA50  and  EMA10 < EMA20

How it fits together:

    system.py (once a day) ──writes──▶ system.json ──read by──▶ website ("System" tab)

    It's a separate file because movers.py rewrites results.json every 20 minutes.

Run (inside your .venv), any time after the market closed:
    python system.py                  # scan the whole market, save system.json
    python system.py --test AAPL,NVTS # show the signal + MAs for some symbols

Same stock list as movers.py (NASDAQ/NYSE/AMEX, no ETFs), price $5 - $90.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone

from movers import (KEY_ID, SECRET, api_get, DATA_URL, ET,
                    get_universe, MIN_PRICE, MAX_PRICE)
from charts import get_bars, DAILY_DAYS, WEEKLY_DAYS

# ---- Settings you can change ------------------------------------------------
SYSTEM_FILE = "system.json"
SCAN_DAYS = 250          # calendar days of history (~170 candles): SMA 50 needs 50,
                         # and the EMAs need extra candles to "settle" like on your chart
MIN_VOLUME = 100_000     # yesterday's volume, same rule as movers.py (skips illiquid stocks)
BATCH_SIZE = 100         # symbols per Alpaca request


# ---- Indicator maths ---------------------------------------------------------
def ema(values, n):
    """Exponential moving average, like thinkorswim's ExpAverage(close, n).

    Each new value = close * k + previous EMA * (1 - k), with k = 2 / (n + 1).
    The first EMA value is simply the first close.
    """
    k = 2 / (n + 1)
    result = []
    for i, v in enumerate(values):
        result.append(v if i == 0 else v * k + result[-1] * (1 - k))
    return result


def check_signal(bars):
    """Run the scan on a list of daily candles (oldest first).

    Returns a dict with the details if the LAST candle fires, else None.
    """
    if len(bars) < 52:                 # not enough history for SMA 50 + 2 candles
        return None

    last_sig = 0                       # 1 = last signal green, -1 = red, 0 = none yet
    first_green = False
    for i in range(2, len(bars)):      # candles 0 and 1 have no 2 previous candles
        combo_high = max(bars[i - 2]["h"], bars[i - 1]["h"])
        combo_low = min(bars[i - 2]["l"], bars[i - 1]["l"])
        green = bars[i]["h"] > combo_high and bars[i]["c"] > combo_high
        red = bars[i]["l"] < combo_low and bars[i]["c"] < combo_low

        first_green = green and last_sig == -1     # uses the signal BEFORE this candle
        if green:
            last_sig = 1
        elif red:
            last_sig = -1

    if not first_green:                # only the last candle (yesterday) matters
        return None

    closes = [b["c"] for b in bars]
    ema10 = ema(closes, 10)[-1]
    ema20 = ema(closes, 20)[-1]
    sma50 = sum(closes[-50:]) / 50
    if not (ema10 > sma50 and ema20 > sma50 and ema10 < ema20):
        return None

    return {
        "ema10": round(ema10, 4), "ema20": round(ema20, 4), "sma50": round(sma50, 4),
        "combo_high": max(bars[-3]["h"], bars[-2]["h"]),
    }


# ---- Downloading -------------------------------------------------------------
def last_closed_day_end(now_utc):
    """Midnight US time today, in UTC. Candles before this = up to yesterday."""
    today_et = now_utc.astimezone(ET).date()
    return datetime(today_et.year, today_et.month, today_et.day, tzinfo=ET).astimezone(timezone.utc)


def download_daily(symbols, start_utc, end_utc):
    """Daily candles for many symbols. Returns {symbol: [bar, bar, ...]} oldest first."""
    all_bars = {s: [] for s in symbols}
    batches = range(0, len(symbols), BATCH_SIZE)
    for n, i in enumerate(batches, 1):
        print(f"  batch {n}/{len(batches)}", end="\r", flush=True)
        params = {
            "symbols": ",".join(symbols[i:i + BATCH_SIZE]),
            "timeframe": "1Day",
            "start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "adjustment": "split",
            "feed": "sip",
            "limit": 10000,
        }
        while True:                     # Alpaca sends big answers in pages
            data = api_get(f"{DATA_URL}/bars", params)
            for sym, bars in (data.get("bars") or {}).items():
                all_bars[sym].extend(bars)
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
            time.sleep(0.3)             # stay below 200 calls/minute
    print()
    return all_bars


# ---- Main --------------------------------------------------------------------
def scan(universe, all_bars, signal_date):
    hits = []
    for sym, bars in all_bars.items():
        # Yesterday must be the last candle (skips stocks that didn't trade yesterday)
        if not bars or bars[-1]["t"][:10] != signal_date:
            continue
        last = bars[-1]
        if not (MIN_PRICE <= last["c"] <= MAX_PRICE) or last["v"] < MIN_VOLUME:
            continue
        info = check_signal(bars)
        if info:
            prev_close = bars[-2]["c"]
            hits.append({
                "symbol": sym,
                "name": universe[sym]["name"],
                "exchange": universe[sym]["exchange"],
                "price": last["c"],
                "change": round(last["c"] - prev_close, 4),
                "change_pct": round((last["c"] - prev_close) / prev_close * 100, 2),
                "volume": last["v"],
                "system": info,
            })
    hits.sort(key=lambda h: h["change_pct"], reverse=True)
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", help="comma-separated symbols: print signal details")
    args = parser.parse_args()

    if not KEY_ID or not SECRET:
        sys.exit("Missing keys. Check .env contains APCA_KEY_ID and APCA_SECRET.")

    now_utc = datetime.now(timezone.utc)
    end_utc = last_closed_day_end(now_utc)
    start_utc = end_utc - timedelta(days=SCAN_DAYS)

    if args.test:
        symbols = [s.strip() for s in args.test.upper().split(",")]
        for sym, bars in download_daily(symbols, start_utc, end_utc).items():
            if not bars:
                print(f"  {sym:<6} no data")
                continue
            closes = [b["c"] for b in bars]
            print(f"  {sym:<6} last candle {bars[-1]['t'][:10]}  close {closes[-1]}  "
                  f"EMA10 {ema(closes, 10)[-1]:.2f}  EMA20 {ema(closes, 20)[-1]:.2f}  "
                  f"SMA50 {sum(closes[-50:]) / 50:.2f}  ->  "
                  f"{'SIGNAL' if check_signal(bars) else 'no signal'}")
        return

    print("1/3 Getting list of US stocks...")
    universe = get_universe()
    symbols = sorted(universe)
    print(f"    {len(symbols)} stocks")

    print(f"2/3 Downloading {SCAN_DAYS} days of daily candles...")
    t0 = time.time()
    all_bars = download_daily(symbols, start_utc, end_utc)
    print(f"    done ({time.time() - t0:.0f} s)")

    # "Yesterday" = the newest candle date in the data (handles weekends + holidays)
    signal_date = max((b[-1]["t"][:10] for b in all_bars.values() if b), default=None)
    if not signal_date:
        sys.exit("No candles downloaded. Is the market data API working?")

    print(f"3/3 Scanning candles of {signal_date}...")
    hits = scan(universe, all_bars, signal_date)

    # Charts for the website cards (same format as charts.py)
    for i, h in enumerate(hits, 1):
        print(f"  charts {i}/{len(hits)}: {h['symbol']:<6}", end="\r", flush=True)
        h["chart_daily"] = get_bars(h["symbol"], "1Day", DAILY_DAYS, now_utc)
        h["chart_weekly"] = get_bars(h["symbol"], "1Week", WEEKLY_DAYS, now_utc)
        time.sleep(0.3)
    print()

    result = {
        "generated_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "signal_date": signal_date,
        "rules": {"min_price": MIN_PRICE, "max_price": MAX_PRICE, "min_volume": MIN_VOLUME,
                  "scan": "first green after red; EMA10 > SMA50, EMA20 > SMA50, EMA10 < EMA20"},
        "count": len(hits),
        "stocks": hits,
    }
    with open(SYSTEM_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Saved {len(hits)} System stocks for {signal_date} to {SYSTEM_FILE}\n")
    for h in hits:
        s = h["system"]
        print(f"  {h['symbol']:<6} ${h['price']:<8} {h['change_pct']:>+6.2f}%  vol {h['volume']:>11,}  "
              f"EMA10 {s['ema10']:.2f} < EMA20 {s['ema20']:.2f}, SMA50 {s['sma50']:.2f}  {h['name'][:30]}")


if __name__ == "__main__":
    main()
