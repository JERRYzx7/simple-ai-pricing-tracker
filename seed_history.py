#!/usr/bin/env python3
"""One-off helper: back-fill a handful of *simulated* historical price
snapshots for the days BEFORE the earliest real (manual/scraped) data point
in the CSV, so the trend chart has some history to show before real price
checks started.

IMPORTANT: rows produced here are tagged source=simulated in the CSV and must
be disclosed as such (see README.md) — this is bootstrap demo data, not real
historical pricing.

Idempotent: re-running this drops any previously-generated simulated rows
before adding a fresh batch, so running it twice doesn't double up the
history (this was a real bug — the first version just appended, and a
second run silently duplicated every simulated data point).

Only reads/writes data/price_snapshots.csv — never touches data/products.csv.
"""

import os
import random
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

SNAPSHOTS_PATH = "data/price_snapshots.csv"
DAYS_BACK = 6
SNAPSHOT_HOUR = 9  # one fixed snapshot per day, matching the real data's fixed capture time
TZ = timezone(timedelta(hours=8))
MAX_DRIFT = 0.04  # +/-4% synthetic price movement per snapshot


def main():
    if not os.path.isfile(SNAPSHOTS_PATH):
        sys.exit(f"{SNAPSHOTS_PATH} not found — run `python fetch_prices.py` first to capture at least one real data point.")

    df = pd.read_csv(SNAPSHOTS_PATH)
    real = df[df["source"].isin(["scraped", "manual"])]
    if real.empty:
        sys.exit("No source=scraped/manual rows in the CSV to use as a simulation baseline — run fetch_prices.py or add one manually first.")

    real = real.copy()
    real["date"] = pd.to_datetime(real["date"]).dt.date
    earliest_date = real["date"].min()
    earliest = real.sort_values("captured_at").groupby("sku").first()

    rows = []
    for sku, base in earliest.iterrows():
        base_price = float(base["price"])
        for day_offset in range(DAYS_BACK, 0, -1):
            sim_date = earliest_date - timedelta(days=day_offset)
            drift = 1 + random.uniform(-MAX_DRIFT, MAX_DRIFT)
            sim_price = round(base_price * drift, 2)
            captured_at = datetime.combine(
                sim_date, datetime.min.time()
            ).replace(hour=SNAPSHOT_HOUR, tzinfo=TZ).isoformat(timespec="seconds")
            rows.append({
                "captured_at": captured_at,
                "date": sim_date.isoformat(),
                "sku": sku,
                "price": sim_price,
                "source": "simulated",
            })

    # Drop old simulated rows before writing the fresh batch — keeps this
    # script idempotent instead of accumulating duplicates on re-run.
    kept = df[df["source"] != "simulated"]
    out = pd.concat([kept, pd.DataFrame(rows)], ignore_index=True)
    out.to_csv(SNAPSHOTS_PATH, index=False)

    print(f"Added {len(rows)} simulated historical snapshots (source=simulated), covering the {DAYS_BACK} days "
          f"before {earliest_date.isoformat()} (the earliest real data point). "
          f"(Dropped {len(df) - len(kept)} old simulated rows first, so re-running this doesn't duplicate data.)")


if __name__ == "__main__":
    main()
