"""
news.py - Phase 2: add recent news to every mover
--------------------------------------------------
Reads results.json (made by movers.py), asks the Alpaca News API (Benzinga)
for each mover's latest headlines, and saves them back into results.json.

How it fits together:

    movers.py ──writes──▶ results.json ──read by──▶ news.py
                                ▲                      │
                                └──── writes back ─────┘   (adds "news" to each mover)

Why a separate step?  Each phase does one job. Later, on AWS, one Lambda
will simply call movers -> news -> AI judge -> charts in a row.

Run (inside your .venv):
    python news.py                      # add news to the movers in results.json
    python news.py --test AAPL,NVDA     # just print news for some symbols (no file change)
"""

import argparse
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone

# Reuse the key check, headers and retry logic we already wrote in movers.py
from movers import KEY_ID, SECRET, api_get, OUTPUT_FILE

# ---- Settings you can change ------------------------------------------------
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
HOURS_BACK = 24          # look at news from the last 24 hours ...
HOURS_BACK_MONDAY = 72   # ... but on Mondays include the weekend
MAX_ARTICLES = 10        # per stock, newest first (enough for the AI to judge)


# ---- Helpers -----------------------------------------------------------------
def clean_text(text):
    """Summaries can contain HTML like '&#39;' or '<p>'. Turn them into plain text."""
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)     # remove HTML tags
    return " ".join(text.split())            # collapse extra spaces/newlines


def news_window_start(now_utc):
    """How far back to look. Monday pre-market should also see weekend news."""
    hours = HOURS_BACK_MONDAY if now_utc.weekday() == 0 else HOURS_BACK
    return now_utc - timedelta(hours=hours)


# ---- Main work ---------------------------------------------------------------
def get_news(symbol, start_utc):
    """Return a short list of recent articles about one stock."""
    data = api_get(NEWS_URL, {
        "symbols": symbol,
        "start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": MAX_ARTICLES,
        "sort": "desc",              # newest first
        "include_content": "false",  # headline + summary is enough, keeps it small
    })
    articles = []
    for n in data.get("news", []):
        articles.append({
            "headline": clean_text(n.get("headline")),
            "summary": clean_text(n.get("summary")),
            "created_at": n.get("created_at"),
            "source": n.get("source"),
            "url": n.get("url"),
            # How many stocks the article is tagged with. A big number usually
            # means a market roundup, not real news about THIS company.
            "symbols": n.get("symbols", []),
        })
    return articles


def add_news(movers, now_utc):
    """Add a 'news' list to every mover (empty list if there is no news)."""
    start_utc = news_window_start(now_utc)
    for i, m in enumerate(movers, 1):
        print(f"  news {i}/{len(movers)}: {m['symbol']}", end="\r", flush=True)
        m["news"] = get_news(m["symbol"], start_utc)
    print()
    return movers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", help="comma-separated symbols, just print their news")
    args = parser.parse_args()

    if not KEY_ID or not SECRET:
        sys.exit("Missing keys. Check .env contains APCA_KEY_ID and APCA_SECRET.")

    now_utc = datetime.now(timezone.utc)

    # Test mode: no results.json needed, handy when the market is closed.
    if args.test:
        start_utc = news_window_start(now_utc)
        for sym in args.test.upper().split(","):
            articles = get_news(sym.strip(), start_utc)
            print(f"\n{sym}: {len(articles)} articles")
            for a in articles:
                print(f"  {a['created_at'][:16]}  [{len(a['symbols'])} tickers]  {a['headline'][:90]}")
        return

    try:
        with open(OUTPUT_FILE) as f:
            result = json.load(f)
    except FileNotFoundError:
        sys.exit(f"{OUTPUT_FILE} not found. Run movers.py first.")

    movers = result.get("movers", [])
    if not movers:
        print(f"No movers in {OUTPUT_FILE}, nothing to do.")
        return

    print(f"Fetching news for {len(movers)} movers...")
    add_news(movers, now_utc)
    result["news_added_at_utc"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    with_news = sum(1 for m in movers if m["news"])
    print(f"Saved. {with_news} of {len(movers)} movers have news.\n")
    for m in movers[:15]:
        top = m["news"][0]["headline"][:70] if m["news"] else "(no news)"
        print(f"  {m['symbol']:<6} {len(m['news']):>2} articles  {top}")


if __name__ == "__main__":
    main()
