"""
Alpaca pre-market / after-hours test
------------------------------------
Fetches 15-minute delayed prices for a few stocks and shows how much
each one has moved since the last regular close.

Your keys are read from a file named .env in the same folder:

    APCA_KEY_ID=your-key-id
    APCA_SECRET=your-secret-key

First time only:
    pip install -r requirements.txt

Then run:
    python alpaca_premarket_test.py

Best time to test (Swedish time):
    Pre-market:   10:30 - 15:30
    After-hours:  22:30 - 02:00
"""

import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# Read the keys from the .env file into environment variables
load_dotenv()

# ---- 1. Settings -----------------------------------------------------------
SYMBOLS = ["AAPL", "TSLA", "NVDA", "AMD", "PLTR", "SOFI", "MARA", "RIVN", "F", "INTC"]

MIN_PERCENT = 4.0   # up at least 4 %
MIN_DOLLARS = 1.0   # ... OR up at least 1 $

URL = "https://data.alpaca.markets/v2/stocks/snapshots"

# ---- 2. Read keys from environment variables -------------------------------
key_id = os.environ.get("APCA_KEY_ID")
secret = os.environ.get("APCA_SECRET")

if not key_id or not secret:
    sys.exit("Missing keys. Check that .env is in the same folder and contains APCA_KEY_ID and APCA_SECRET.")

headers = {
    "APCA-API-KEY-ID": key_id,
    "APCA-API-SECRET-KEY": secret,
}

# delayed_sip = whole US market, 15 min delayed (allowed on the free plan)
params = {
    "symbols": ",".join(SYMBOLS),
    "feed": "delayed_sip",
}

# ---- 3. Ask Alpaca (ONE call for all symbols) -------------------------------
response = requests.get(URL, headers=headers, params=params, timeout=15)

if response.status_code != 200:
    sys.exit(f"Error {response.status_code}: {response.text}")

data = response.json()

# ---- 4. Compare latest price with the last regular close --------------------
today_utc = datetime.now(timezone.utc).date()


def last_regular_close(snap):
    """
    dailyBar = the most recent regular-session day bar.
    If that bar is from today, the session has already started, so the
    'previous close' is prevDailyBar. Otherwise dailyBar IS the previous close.
    """
    daily = snap.get("dailyBar") or {}
    prev = snap.get("prevDailyBar") or {}
    if daily.get("t", "")[:10] == str(today_utc):
        return prev.get("c")
    return daily.get("c")


print(f"\n{'SYMBOL':<7}{'LAST':>10}{'CLOSE':>10}{'CHANGE $':>10}{'CHANGE %':>10}  {'TRADE TIME (UTC)':<20} MOVER?")
print("-" * 80)

for symbol in SYMBOLS:
    snap = data.get(symbol)
    if not snap:
        print(f"{symbol:<7} no data")
        continue

    trade = snap.get("latestTrade") or {}
    last_price = trade.get("p")
    trade_time = trade.get("t", "")[:19].replace("T", " ")
    close = last_regular_close(snap)

    if last_price is None or not close:
        print(f"{symbol:<7} missing price data")
        continue

    change = last_price - close
    pct = change / close * 100
    is_mover = pct >= MIN_PERCENT or change >= MIN_DOLLARS

    print(f"{symbol:<7}{last_price:>10.2f}{close:>10.2f}{change:>+10.2f}{pct:>+9.2f}%  {trade_time:<20} {'YES' if is_mover else ''}")

print("\nCheck the TRADE TIME column: during pre-market it should be today, after 09:00 UTC.")
