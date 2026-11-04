"""
movers.py - Phase 1: find pre-market / after-hours movers
-----------------------------------------------------------
Scans the whole US stock market through Alpaca (15-min delayed, free plan),
keeps the stocks that moved up enough, and saves them to results.json.
 
How it works:
  1. Get the list of all tradable US stocks         (1 call)
  2. Get the latest price of every stock, 200 at a time  (~50 calls)
  3. Keep stocks up >= 4 %  OR  >= $1, AND up >= $0.20, AND price $5 - $90
     (ETFs and funds are skipped: they have no company news)
  4. For those, add up the volume traded this session    (a few calls)
  5. Drop low-volume "fake movers", save results.json
 
Run (inside your .venv):
    python movers.py              # detects the session automatically
    python movers.py --session pre    # force pre-market   (for testing)
    python movers.py --session post   # force after-hours  (for testing)
 
Sessions in Swedish time (winter):
    Pre-market:  10:00 - 15:30      After-hours: 22:00 - 02:00
"""
 
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
 
import requests
from dotenv import load_dotenv
 
load_dotenv()
 
# ---- Settings you can change ------------------------------------------------
MIN_PERCENT = 4.0            # up at least 4 % ...
MIN_DOLLARS = 1.0            # ... OR up at least $1
MIN_PRICE = 5.00             # only stocks priced $5 or more ...
MAX_PRICE = 90.00            # ... and $90 or less
MIN_CHANGE = 0.20            # must be up at least $0.20 (always required)
MIN_SESSION_VOLUME = 100_000 # shares traded this session, removes "fake movers"
BATCH_SIZE = 200             # symbols per snapshot request
WORKERS = 5                  # batches fetched at the same time
OUTPUT_FILE = "results.json"
 
# Real US exchanges, no OTC. ARCA and BATS are left out on purpose: almost
# everything listed there is an ETF/fund (one known exception: Cboe itself).
EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}

# ETFs and funds have no company news, so the AI would always reject them.
# Alpaca has no "is ETF" flag, so we recognise them by words in their name.
# (Don't add "Trust": many real companies are called "... Trust".)
FUND_WORDS = re.compile(r"\b(ETF|ETFs|ETN|Fund|Funds|ProShares|Direxion|iShares|SPDR|QQQ Trust)\b")
 
TRADING_URL = "https://paper-api.alpaca.markets/v2/assets"
DATA_URL = "https://data.alpaca.markets/v2/stocks"
DELAY = timedelta(minutes=16)   # free plan: data must be older than 15 min
 
ET = ZoneInfo("America/New_York")   # US market time
 
# ---- Keys --------------------------------------------------------------------
KEY_ID = os.environ.get("APCA_KEY_ID")
SECRET = os.environ.get("APCA_SECRET")
HEADERS = {"APCA-API-KEY-ID": KEY_ID or "", "APCA-API-SECRET-KEY": SECRET or ""}
 
 
# ---- Helpers -----------------------------------------------------------------
def api_get(url, params=None):
    """GET with a simple retry if Alpaca says 'too many requests' (429)."""
    for attempt in range(3):
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code != 200:
            sys.exit(f"Error {r.status_code} from {url}: {r.text[:300]}")
        return r.json()
    sys.exit("Alpaca kept saying 'too many requests'. Try again in a minute.")
 
 
def detect_session(now_et):
    """Which part of the US trading day are we in? (weekends = closed)"""
    if now_et.weekday() >= 5:
        return "closed"
    t = now_et.time()
    if datetime.strptime("04:00", "%H:%M").time() <= t < datetime.strptime("09:30", "%H:%M").time():
        return "pre"
    if datetime.strptime("09:30", "%H:%M").time() <= t < datetime.strptime("16:00", "%H:%M").time():
        return "regular"
    if datetime.strptime("16:00", "%H:%M").time() <= t < datetime.strptime("20:00", "%H:%M").time():
        return "post"
    return "closed"
 
 
def session_start(session, today_et):
    """When did this session begin (as a UTC time)?"""
    hour, minute = (4, 0) if session == "pre" else (16, 0)
    start = datetime(today_et.year, today_et.month, today_et.day, hour, minute, tzinfo=ET)
    return start.astimezone(timezone.utc)
 
 
def reference_close(snap, session, today_et):
    """
    The price we compare against:
      pre-market  -> the LAST regular close (yesterday, or Friday on a Monday)
      after-hours -> TODAY's regular close
    dailyBar = most recent regular-session day, prevDailyBar = the one before.
    """
    daily = snap.get("dailyBar") or {}
    prev = snap.get("prevDailyBar") or {}
    daily_is_today = daily.get("t", "")[:10] == today_et.isoformat()
 
    if session == "post":
        return daily.get("c") if daily_is_today else None
    # pre-market
    return prev.get("c") if daily_is_today else daily.get("c")
 
 
def is_mover(price, ref):
    change = price - ref
    pct = change / ref * 100
    ok = (pct >= MIN_PERCENT or change >= MIN_DOLLARS) and change >= MIN_CHANGE
    return ok, change, pct
 
 
def parse_time(ts):
    """Alpaca timestamps look like 2026-11-04T09:31:12.123456789Z"""
    return datetime.fromisoformat(ts[:19]).replace(tzinfo=timezone.utc)
 
 
# ---- Step 1: all tradable US stocks -----------------------------------------
def get_universe():
    assets = api_get(TRADING_URL, {"status": "active", "asset_class": "us_equity"})
    universe = {}
    for a in assets:
        if (a.get("tradable") and a.get("exchange") in EXCHANGES and "/" not in a["symbol"]
                and not FUND_WORDS.search(a.get("name", ""))):
            universe[a["symbol"]] = {"name": a.get("name", ""), "exchange": a["exchange"]}
    return universe
 
 
# ---- Step 2 + 3: snapshots in batches, price filter --------------------------
def fetch_snapshots(batch):
    return api_get(f"{DATA_URL}/snapshots",
                   {"symbols": ",".join(batch), "feed": "delayed_sip"})
 
 
def find_candidates(universe, session, today_et, start_utc):
    symbols = sorted(universe)
    batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
    candidates = []
    done = 0
 
    # Ask for several batches at the same time instead of one by one.
    # ~55 calls in total, far below the free limit of 200 calls/minute.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for snaps in pool.map(fetch_snapshots, batches):
            done += 1
            print(f"  batch {done}/{len(batches)}", end="\r", flush=True)
            candidates.extend(check_batch(snaps, universe, session, today_et, start_utc))
    print()
    return candidates
 
 
def check_batch(snaps, universe, session, today_et, start_utc):
    """Apply the filter rules to one batch of snapshots."""
    candidates = []
    for sym, snap in snaps.items():
        if not snap:
            continue
        trade = snap.get("latestTrade") or {}
        price, ts = trade.get("p"), trade.get("t")
        if not price or not ts or not (MIN_PRICE <= price <= MAX_PRICE):
            continue
        if parse_time(ts) < start_utc:      # no trade yet this session
            continue
        ref = reference_close(snap, session, today_et)
        if not ref:
            continue
        ok, change, pct = is_mover(price, ref)
        if ok:
            candidates.append({
                "symbol": sym,
                "name": universe[sym]["name"],
                "exchange": universe[sym]["exchange"],
                "price": round(price, 4),
                "ref_close": round(ref, 4),
                "change": round(change, 4),
                "change_pct": round(pct, 2),
                "last_trade_utc": ts[:19] + "Z",
            })
    return candidates
 
 
# ---- Step 4 + 5: session volume ---------------------------------------------
def add_session_volume(candidates, start_utc, end_utc):
    """Sum the 1-minute bar volumes since the session started."""
    volume = {c["symbol"]: 0 for c in candidates}
    symbols = list(volume)
    for i in range(0, len(symbols), 100):
        params = {
            "symbols": ",".join(symbols[i:i + 100]),
            "timeframe": "1Min",
            "start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "feed": "sip",          # allowed because 'end' is >15 min ago
            "limit": 10000,
        }
        while True:
            data = api_get(f"{DATA_URL}/bars", params)
            for sym, bars in (data.get("bars") or {}).items():
                volume[sym] += sum(b.get("v", 0) for b in bars)
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
            time.sleep(0.35)
    for c in candidates:
        c["session_volume"] = volume[c["symbol"]]
    return [c for c in candidates if c["session_volume"] >= MIN_SESSION_VOLUME]
 
 
# ---- Main --------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", choices=["pre", "post"], help="force a session for testing")
    args = parser.parse_args()
 
    if not KEY_ID or not SECRET:
        sys.exit("Missing keys. Check .env contains APCA_KEY_ID and APCA_SECRET.")
 
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET)
    today_et = now_et.date()
    session = args.session or detect_session(now_et)
 
    if session not in ("pre", "post"):
        sys.exit(f"Market session is '{session}' right now. "
                 "Run during pre-market or after-hours, or use --session pre/post to test.")
 
    start_utc = session_start(session, today_et)
    end_utc = now_utc - DELAY
 
    print(f"Session: {session}  |  US time now: {now_et:%Y-%m-%d %H:%M}")
    print("1/3 Getting list of US stocks...")
    universe = get_universe()
    print(f"    {len(universe)} stocks")
 
    print("2/3 Scanning prices...")
    t0 = time.time()
    candidates = find_candidates(universe, session, today_et, start_utc)
    print(f"    {len(candidates)} stocks moved enough  ({time.time() - t0:.0f} s)")
 
    movers = []
    if candidates and end_utc > start_utc:
        print("3/3 Checking volume...")
        movers = add_session_volume(candidates, start_utc, end_utc)
    elif candidates:
        print("3/3 Session started < 16 min ago, no volume data yet - skipping volume check")
        movers = candidates
 
    movers.sort(key=lambda m: m["change_pct"], reverse=True)
 
    result = {
        "generated_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session": session,
        "data_delay_minutes": 15,
        "rules": {"min_percent": MIN_PERCENT, "min_dollars": MIN_DOLLARS,
                  "min_change": MIN_CHANGE, "min_price": MIN_PRICE,
                  "max_price": MAX_PRICE,
                  "min_session_volume": MIN_SESSION_VOLUME},
        "count": len(movers),
        "movers": movers,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)
 
    print(f"\nSaved {len(movers)} movers to {OUTPUT_FILE}\n")
    for m in movers[:15]:
        print(f"  {m['symbol']:<6} {m['change_pct']:>+7.2f}%  ${m['price']:<9} "
              f"vol {m['session_volume']:>10,}  {m['name'][:35]}")
 
 
if __name__ == "__main__":
    main()