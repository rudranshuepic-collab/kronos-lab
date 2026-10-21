# Kronos Lab

A free website that runs the open-source [Kronos](https://github.com/shiyu-coder/Kronos) forecasting
model (Kronos-small, MIT licence) on NSE stocks every weekday evening.

- **Forecast**: a cone of eight possible price paths for the next 20 trading days.
- **Scanner**: the Nifty 50 plus your watchlist, ranked by Kronos's expected return.
- **Reality check**: a walk-forward test on months after Kronos's June 2024 training cutoff,
  with costs, compared with momentum and an equal-weight Nifty 50.

Research tool only. It places no orders and is not investment advice.

## How it runs (₹0)

GitHub Actions runs `python -m app.build_site` at about 5 pm IST, Monday to Friday. The script downloads
daily candles from Yahoo Finance, runs Kronos on a free GitHub runner, writes every result as
JSON, and publishes the site to GitHub Pages. There is no server between runs.

- **Add a stock:** edit `watchlist.txt` on GitHub. The site rebuilds automatically.
- **Update now:** Actions tab → *Daily Kronos update* → *Run workflow*.

## One-time setup

Settings → Pages → *Build and deployment* → Source: **GitHub Actions**.

## Run locally

```bash
pip install -r requirements.txt
python -m app.build_site            # about 3 minutes on 2 CPU cores
python -m http.server -d site 8000  # open http://localhost:8000
```

## Layout

| Path | What it is |
|---|---|
| `app/engine.py` | Kronos wrapper: batched, seeded, KV-cached (identical output, ~8x faster on CPU) |
| `app/data.py` | Yahoo Finance daily candles (split/dividend adjusted), Nifty 50 list |
| `app/reality.py` | Walk-forward test and its statistics |
| `app/build_site.py` | Nightly build: forecasts, ranking, reality check, static site |
| `app/kronos_model/` | Vendored Kronos model code (MIT, © the Kronos authors) |
| `web/` | The website (plain HTML, CSS and JS, no build step) |
| `data/reality_check.json` | Cached test results, extended once a month |
| `watchlist.txt` | Your extra symbols |
