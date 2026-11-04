"""
judge.py - Phase 3: let an AI label each mover's news
------------------------------------------------------
Reads results.json (movers + news), asks Claude about each mover's headlines,
and saves the answer back as  "ai": {"label": "...", "reason": "..."}.

The three labels:
    "Solid"      hard news: earnings beat, FDA approval, signed contract, buyout ...
    "Just talk"  soft news: CEO comments, interviews, plans, "potential" deals
    "No"         nothing useful: analyst chatter, roundups, bad news, no news

How it fits together:

    movers.py ──▶ news.py ──▶ judge.py ──▶ results.json
                                  │
                                  ▼
                      Claude API  (one question per stock)
                      "What kind of news is this?"  ──▶  {"label": ..., "reason": ...}

Why keep the "No" stocks too?  While testing, you can see WHY a stock was
rejected. The website will show "Solid" and "Just talk" stocks, each with its label.

Why a cache?  The pipeline runs every 20 minutes. Most stocks still have the
same news as last time, so we reuse the old answer instead of paying again.

Run (inside your .venv):
    python judge.py                     # judge the movers in results.json
    python judge.py --test AAPL,NVDA    # fetch news + judge some symbols, just print

Needs ANTHROPIC_API_KEY in .env (get one at https://console.anthropic.com).
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import anthropic

# Reuse what we already built in the earlier phases
from movers import OUTPUT_FILE            # also loads .env
from news import get_news, news_window_start

# ---- Settings you can change ------------------------------------------------
MODEL = "claude-haiku-5-5"   # cheapest Claude model; sorting headlines is an easy task
EFFORT = "low"               # low effort = faster + cheaper
CACHE_FILE = "judge_cache.json"

LABELS = ["Solid", "Just talk", "No"]
SHOW_ON_SITE = {"Solid", "Just talk"}   # labels the website will display

# The rules the AI follows. Change this text to change what gets which label.
RULES = """You label the news behind a US stock that is moving up in pre-market or
after-hours trading. Pick exactly one label.

"Solid" - hard, confirmed, company-specific news:
- earnings beat, or raised guidance / forecast
- FDA approval or positive trial results
- a signed big new contract, partnership or customer
- a buyout / merger offer for the company
- other confirmed company-specific events of the same weight

"Just talk" - positive words about the company's OWN business, but nothing confirmed:
- CEO or management comments about the company's own products, customers,
  demand, sales or plans (e.g. "CEO says demand for our chips is accelerating")
  Comments about other topics (the economy, markets, gold, politics, the
  industry in general) do NOT count - label those "No".
- plans, hopes, targets, or "potential" / non-binding deals (e.g. an MOU)
- product announcements without hard numbers or signed customers

"No" - nothing that explains the move:
- "stocks moving in pre-market" / "top movers" roundup articles
  (a hint: they are tagged with many tickers)
- analyst ratings, price-target changes, "maintains buy" articles
- share offerings, dilution, or other news that is bad for the stock
- old news, rumours, or general market / sector news
- unclear headlines you can't be sure about
- no news at all

Look at all the articles together, about THIS company only.
If any article is Solid, answer "Solid". Otherwise, if any is Just talk,
answer "Just talk". Otherwise answer "No".

Write the reason as one plain-English sentence of at most 20 words that a
website visitor can read, naming the news
(e.g. "Q3 earnings beat estimates and the company raised full-year guidance.")."""

# The exact shape of the answer we want back. Structured output makes Claude
# always reply with valid JSON in this shape, and "enum" means the label can
# only be one of our three words - never a typo like "solid" or "Talk".
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": LABELS},
        "reason": {"type": "string"},
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}


# ---- Helpers -----------------------------------------------------------------
def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def cache_key(mover):
    """Same symbol + same newest article = same news, so same answer.

    A short fingerprint of MODEL + RULES is added too: if you edit the rules
    or change the model, old cached answers are ignored and stocks are re-judged.
    """
    newest = mover["news"][0]["created_at"] if mover["news"] else "none"
    rules_id = hashlib.md5((MODEL + RULES).encode()).hexdigest()[:8]
    return f"{mover['symbol']}|{newest}|{rules_id}"


def build_question(mover):
    """Turn one mover and its articles into the text we send to Claude."""
    lines = [f"Stock: {mover['symbol']} ({mover.get('name', '')})"]
    if "change_pct" in mover:
        lines.append(f"Move: {mover['change_pct']:+.2f}% in {mover.get('session', '')} trading")
    lines.append("")
    lines.append("Recent articles, newest first:")
    for i, a in enumerate(mover["news"], 1):
        lines.append(f"{i}. [{a['created_at'][:16]}] [tagged with {len(a['symbols'])} tickers] {a['headline']}")
        if a["summary"]:
            lines.append(f"   Summary: {a['summary']}")
    return "\n".join(lines)


# ---- Main work ---------------------------------------------------------------
def ask_claude(client, mover):
    """Ask Claude about one stock. Returns {"label": "Solid"/"Just talk"/"No", "reason": str}."""
    if not mover["news"]:
        # No articles -> nothing to judge, and no need to pay for an API call
        return {"label": "No", "reason": "No news found."}

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,                       # room for thinking + the short answer
        system=RULES,
        messages=[{"role": "user", "content": build_question(mover)}],
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
        },
    )

    # Claude can stop early: declined, or ran out of room. Treat as "No".
    if response.stop_reason == "refusal":
        return {"label": "No", "reason": "AI declined to judge this news."}
    if response.stop_reason == "max_tokens":
        return {"label": "No", "reason": "AI answer was cut off."}

    # The reply can contain "thinking" blocks first; the answer is the text block.
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def judge_movers(client, movers):
    """Add an "ai" verdict to every mover, reusing cached answers when possible."""
    cache = load_cache()
    asked = 0
    for i, m in enumerate(movers, 1):
        print(f"  judging {i}/{len(movers)}: {m['symbol']:<6}", end="\r", flush=True)
        key = cache_key(m)
        if key not in cache:
            cache[key] = ask_claude(client, m)
            if m["news"]:          # stocks without news never reach the AI
                asked += 1
        m["ai"] = cache[key]
    print()

    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
    return asked


def make_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Missing key. Add ANTHROPIC_API_KEY=... to .env "
                 "(create one at https://console.anthropic.com).")
    return anthropic.Anthropic()   # reads ANTHROPIC_API_KEY, retries 429/5xx itself


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", help="comma-separated symbols: fetch news, judge, just print")
    args = parser.parse_args()

    client = make_client()
    now_utc = datetime.now(timezone.utc)

    try:
        # Test mode: works any time, no results.json needed
        if args.test:
            start_utc = news_window_start(now_utc)
            for sym in args.test.upper().split(","):
                sym = sym.strip()
                mover = {"symbol": sym, "news": get_news(sym, start_utc)}
                verdict = ask_claude(client, mover)
                print(f"{sym:<6} {verdict['label']:<9}  ({len(mover['news'])} articles)  {verdict['reason']}")
            return

        try:
            with open(OUTPUT_FILE) as f:
                result = json.load(f)
        except FileNotFoundError:
            sys.exit(f"{OUTPUT_FILE} not found. Run movers.py and news.py first.")

        movers = result.get("movers", [])
        if not movers:
            print(f"No movers in {OUTPUT_FILE}, nothing to do.")
            return
        if any("news" not in m for m in movers):
            sys.exit("Some movers have no 'news' yet. Run news.py first.")

        for m in movers:
            m["session"] = result.get("session", "")

        print(f"Judging {len(movers)} movers with {MODEL}...")
        asked = judge_movers(client, movers)

    except anthropic.AuthenticationError:
        sys.exit("Claude API says the key is wrong. Check ANTHROPIC_API_KEY in .env.")
    except anthropic.APIConnectionError:
        sys.exit("Could not reach the Claude API. Check your internet connection.")
    except anthropic.APIStatusError as e:
        sys.exit(f"Claude API error {e.status_code}: {e.message}")

    result["judged_at_utc"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    # How many stocks got each label, e.g. {"Solid": 3, "Just talk": 1, "No": 8}
    result["label_counts"] = {lab: sum(1 for m in movers if m["ai"]["label"] == lab)
                              for lab in LABELS}
    result.pop("solid_count", None)          # old field from the yes/no version

    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    c = result["label_counts"]
    print(f"Saved. {len(movers)} movers: {c['Solid']} Solid, {c['Just talk']} Just talk, "
          f"{c['No']} No  ({asked} AI calls this run).\n")
    for m in movers[:20]:
        print(f"  {m['symbol']:<6} {m['ai']['label']:<9}  {m['ai']['reason'][:80]}")


if __name__ == "__main__":
    main()
