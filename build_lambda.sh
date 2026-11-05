#!/usr/bin/env bash
# build_lambda.sh - put our code + its libraries in one folder for AWS Lambda
# ---------------------------------------------------------------------------
# Lambda runs Linux on Intel/AMD (x86_64) with Python 3.13. Some libraries
# (e.g. pydantic, used by anthropic) contain compiled code, so we ask pip for
# the versions built for exactly that system - not for your laptop's Python.
#
#   requirements.txt ──pip──▶ build/package/   ◀── our .py files
#                                   │
#                     terraform zips this folder and uploads it to Lambda
#
# Run from the project folder:  ./build_lambda.sh   (deploy.sh does it for you)
set -euo pipefail
cd "$(dirname "$0")"

rm -rf build
mkdir -p build/package

# Use the project's .venv pip (works even if the venv isn't activated)
PIP=".venv/bin/pip"
[[ -x "$PIP" ]] || PIP="pip"

"$PIP" install -r requirements.txt \
    --target build/package \
    --platform manylinux2014_x86_64 \
    --python-version 3.13 \
    --implementation cp \
    --only-binary=:all: \
    --quiet

# Only the scripts Lambda needs (no test files, no .env!)
cp movers.py news.py judge.py charts.py lambda_function.py build/package/

echo "Built build/package ($(du -sh build/package | cut -f1))"
