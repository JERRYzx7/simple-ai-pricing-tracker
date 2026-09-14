# AI-Powered PC Pricing Tracker

Application assessment for the Lenovo AI Application Development Intern position (WD00102927).

## Quick start

Python version and virtualenv are managed with [uv](https://docs.astral.sh/uv/) (`.python-version` pins 3.12):

1. `uv venv --python 3.12 .venv` (downloads that Python version automatically on first run if it's not already installed locally)
2. `uv pip install --python .venv/bin/python -r requirements.txt`
3. Requires Google Chrome (the scraper drives a real Chrome window via Selenium — a browser window will pop up when it runs)
4. `.venv/bin/python fetch_prices.py` to capture one real snapshot (reads `data/products.csv`, appends to `data/price_snapshots.csv`)
5. `.venv/bin/python seed_history.py` to back-fill a few days of simulated history so the trend chart has something to show right away
6. `cp .env.example .env`, get a free API key at https://aistudio.google.com/ and set `GOOGLE_API_KEY` (needed for the AI chat panel — without it the panel just shows a setup message, the rest of the app still works)
7. `.venv/bin/streamlit run app.py` to open the interactive app

(You can also `source .venv/bin/activate` and use `python` / `streamlit` directly — same effect.)

> Why uv: this machine is Apple Silicon, but the system's default `python3` is an
> older x86_64 Python 3.9 running under Rosetta. A plain `pip install` of streamlit
> pulls in pyarrow, which has no prebuilt wheel for that interpreter and fails
> trying to compile from source. uv installs a native arm64 Python 3.12, where
> every dependency has a prebuilt wheel — faster install, no build failures.
>
> Don't have uv / don't want to install it? A plain `python3 -m venv .venv`,
> `source .venv/bin/activate` (or `.venv\Scripts\activate` on Windows), then
> `pip install -r requirements.txt` works fine on most machines — this project
> only needed uv to work around this particular dev machine's Rosetta-emulated
> Python. If that plain `pip install` also tries to compile pyarrow from source
> and fails, that's the same symptom — installing uv (`curl -LsSf
> https://astral.sh/uv/install.sh | sh`) and using steps 1-2 above is the
> quickest fix.

## Changing the tracked products

Edit `data/products.csv` — this is the static product catalog, maintained by hand; the schedule / `fetch_prices.py` only reads it, never writes to it. Columns: `group_id, group_name, retailer, description, os, form_factor, processor, ram_gb, storage_gb, url, sku, display_name`. `url` is the Best Buy product page link; `sku` is the number after `/sku/` in that URL (if left blank, the script parses it out automatically with a regex).

## Automating price snapshots (scheduling)

With crontab, twice a day (09:00 and 21:00):

```
0 9,21 * * * /absolute/path/to/lenovo/run_snapshot.sh
```

Or with macOS launchd: wrap `run_snapshot.sh` in a plist and set `StartCalendarInterval` accordingly.
Each run's log is written to `logs/snapshot.log`.

## Project structure

```
data/products.csv         Product catalog (static, hand-maintained): equivalence group definition + specs/urls for the 3 tracked products
data/price_snapshots.csv  Price time series (only captured_at/date/sku/price/source — everything else is joined in from products.csv by sku)
fetch_prices.py            Scrapes each product page in products.csv via Selenium, appending one snapshot row per product
seed_history.py            Back-fills simulated historical snapshots (demo only, tagged source=simulated in the CSV)
run_snapshot.sh             Wrapper script for scheduling
app.py                      Streamlit app: group selector / sidebar hardware & price filters (checkboxes) / comparison table / trend chart / floating AI chat bubble in the bottom right
```

## Known limitations

- **The scraper is subject to intermittent rate limiting**: too many requests to bestbuy.com in a short window triggers what looks like a temporary IP-level block, and the whole run fails until it clears. The schedule is designed around this (1-2 runs/day); when a run does get blocked, `fetch_prices.py` logs the error and skips the affected products rather than crashing, but that snapshot will have missing data.
- The scraper **requires a real, non-headless Chrome window** — a scheduled run will briefly pop up a browser window on the machine running it. Both headless mode and reusing one browser session across multiple product pages get blocked by Best Buy's anti-bot layer (verified through testing).
- Data produced by `seed_history.py` is simulated, not real historical pricing — the CSV's `source` column (`scraped` / `simulated`) and the app UI both disclose this.
- This product group's `form_factor` isn't fully consistent (the Lenovo is a 2-in-1, the Acer/Dell are clamshells) — a deliberate equivalence judgment call.
- The AI chat panel is wired up to Google AI Studio (Gemini, free tier) using function calling, so the model calls `list_products()` / `get_price_summary()` / `get_price_history()` to answer from real data. It's been verified end-to-end with a real API key, including a test case that surfaced a real tool gap and led to adding `get_price_history`.
