"""
backfill.py — fill the ledger with forecasts for past trading days, as if run that evening.

Each day only sees candles up to that day, and every date is after Kronos's June 2024
training cutoff, so nothing is leaked. Rows are labelled source=backfill so the site
can show them apart from forecasts recorded live. Existing live rows are never touched.

    python -m app.backfill 40        # the last 40 trading days before the latest one
"""
from __future__ import annotations

import logging
import sys
import time

from . import ledger
from .build_site import forecast_universe, read_watchlist
from .data import NIFTY50, display_symbol, get_many, to_yahoo
from .engine import DEFAULT_LOOKBACK, MODEL_ID, KronosEngine

log = logging.getLogger("kronos.backfill")


def main(days: int) -> int:
    engine = KronosEngine()
    engine.load()
    watch = read_watchlist()
    symbols = list(dict.fromkeys(NIFTY50 + watch))
    hist = {display_symbol(k): v for k, v in get_many([to_yahoo(s) for s in symbols], period="3y").items()}
    cal = sorted(set().union(*[set(df.index) for s, df in hist.items() if s in NIFTY50]))
    dates = cal[-(days + 1):-1]          # leave the latest session for the live run
    done = set(ledger.load()["signal_date"])
    for k, d in enumerate(dates):
        ds = d.strftime("%Y-%m-%d")
        if ds in done:
            continue
        t0 = time.time()
        frames = {}
        for s, df in hist.items():
            past = df.loc[:d]
            if len(past) >= DEFAULT_LOOKBACK and past.index[-1] == d:
                frames[s] = past
        rows, _ = forecast_universe(engine, frames, watch)
        n = ledger.record(rows, ds, "backfill", MODEL_ID)
        log.info("[%d/%d] %s: %d forecasts, picks %s (%.0fs)", k + 1, len(dates), ds, n,
                 [r["symbol"] for r in rows if r["pick"]], time.time() - t0)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "huggingface_hub", "yfinance", "kronos.build"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 40))
