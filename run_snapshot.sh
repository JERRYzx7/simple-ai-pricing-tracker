#!/usr/bin/env bash
# Wrapper for cron/launchd: run the price snapshot job and log output.
#
# Example crontab entry (runs twice a day, 09:00 and 21:00):
#   0 9,21 * * * /absolute/path/to/lenovo/run_snapshot.sh
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs

if [ -d .venv ]; then
  source .venv/bin/activate
fi

python3 fetch_prices.py >> logs/snapshot.log 2>&1
