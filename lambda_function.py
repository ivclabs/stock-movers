"""
lambda_function.py - Phase 6: runs our scripts inside AWS Lambda
-----------------------------------------------------------------
The scripts (movers.py, news.py, ...) are the same ones you run on your laptop.
This file only adds what is different in the cloud:

  * Lambda may only write files in /tmp, so we work in /tmp.
  * Files must survive between runs, so they live in S3 instead of your disk.
  * Keys come from Lambda environment variables instead of .env.

Entry point ("handler") of the Lambda function:

  pipeline_handler  every 20 min in pre-market / after-hours
      S3 judge_cache.json ─▶ /tmp ─▶ movers → news → judge → charts
                                          └──▶ S3: results.json + judge_cache.json

If a step fails, nothing is uploaded: the website keeps showing the last good data.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import boto3          # AWS's Python library, already installed inside Lambda

import movers
import news
import judge
import charts

BUCKET = os.environ.get("BUCKET", "")
WORK_DIR = "/tmp"
SETTLE = timedelta(minutes=20)   # skip the first 20 min of a session: no volume data yet

s3 = boto3.client("s3")


# ---- Helpers -----------------------------------------------------------------
def run_step(name, main_function):
    """Run one script's main(), the same as `python script.py` on your laptop.

    The scripts stop with sys.exit("message") when something is wrong.
    Here that becomes an error, so the run stops and nothing is uploaded.
    """
    print(f"--- {name} ---")
    sys.argv = [name]                    # main() reads command-line options; give it none
    try:
        main_function()
    except SystemExit as stop:
        if stop.code not in (None, 0):
            raise RuntimeError(f"{name} stopped: {stop.code}") from None


def download(key):
    """Copy a file from S3 to /tmp. Missing file (first run) is fine."""
    try:
        s3.download_file(BUCKET, key, os.path.join(WORK_DIR, key))
        print(f"downloaded s3://{BUCKET}/{key}")
    except s3.exceptions.ClientError:
        print(f"no {key} in S3 yet, starting fresh")


def upload(key, cache_seconds=60):
    """Copy a file from /tmp to S3.

    Cache-Control tells CloudFront and browsers to keep a copy for at most
    `cache_seconds`, so visitors see new data within a minute.
    """
    s3.upload_file(os.path.join(WORK_DIR, key), BUCKET, key, ExtraArgs={
        "ContentType": "application/json",
        "CacheControl": f"max-age={cache_seconds}",
    })
    print(f"uploaded s3://{BUCKET}/{key}")


# ---- Handlers ----------------------------------------------------------------
def pipeline_handler(event, context):
    """movers → news → judge → charts, then upload results.json."""
    os.chdir(WORK_DIR)

    # The schedules only run in pre-market / after-hours, but double-check
    # (and skip the first minutes of a session, when there is no volume data yet).
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(movers.ET)
    session = movers.detect_session(now_et)
    if session not in ("pre", "post"):
        print(f"Session is '{session}', nothing to do.")
        return {"skipped": session}
    if now_utc - movers.session_start(session, now_et.date()) < SETTLE:
        print("Session just started, waiting for volume data.")
        return {"skipped": "session just started"}

    download(judge.CACHE_FILE)           # saved AI answers from earlier runs

    run_step("movers", movers.main)
    run_step("news", news.main)
    run_step("judge", judge.main)
    run_step("charts", charts.main)

    upload(movers.OUTPUT_FILE)
    upload(judge.CACHE_FILE, cache_seconds=0)
    return {"ok": True, "session": session}

