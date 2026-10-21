"""
build_site.py — run Kronos once and write the whole website as static files.

GitHub Actions runs this every weekday evening after NSE closes, then publishes
site/ to GitHub Pages. Nothing runs between builds, so there's no server to host.

    python -m app.build_site            # writes ./site
    python -m http.server -d site 8000  # preview at http://localhost:8000

Steps:
  1. Forecast every stock in the Nifty 50 + watchlist.txt (20 trading days, 8 paths each).
  2. Pick the top 5 Nifty 50 names and attach a trade plan (buy, size, sell date, stop).
  3. Save today's forecasts to the ledger and score every past forecast whose 20 days are up.
  4. Extend the Reality Check with any newly testable month (cached in data/).
  5. Copy the web app (web/) next to the JSON.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import ledger, reality
from .data import NIFTY50, display_symbol, get_many, to_yahoo
from .engine import DEFAULT_LOOKBACK, MODEL_ID, TRAINING_DATA_END, KronosEngine

log = logging.getLogger("kronos.build")

# ── Config ───────────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.environ.get("KRONOS_SITE_DIR", os.path.join(ROOT, "site"))
WEB_DIR = os.path.join(ROOT, "web")
WATCHLIST_FILE = os.path.join(ROOT, "watchlist.txt")
HORIZON = 20
SAMPLES = 8
CHUNK = 6                   # stocks per inference batch (x8 samples = 48 sequences)
HISTORY_BARS_SHOWN = 120
MAX_SYMBOLS = 150           # keeps the nightly run well inside GitHub's free minutes
TOP_PICKS = 5               # few names = DP charges stay small on a ₹50k-₹1L account
DEFAULT_CAPITAL = 100_000   # the page lets you change this; it only sets the default
MAX_GAP_UP = 0.02           # skip a buy if it opens >2% above the signal close
STOP_ATR = 3.0              # emergency stop distance (untested for this strategy)
TRACK_DAYS_SHOWN = 90       # calendar days of picks shown on Track record
IST = timezone(timedelta(hours=5, minutes=30))


def slug(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9-]", "_", symbol.upper())


def read_watchlist() -> list[str]:
    try:
        with open(WATCHLIST_FILE) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        s = line.split("#", 1)[0].strip().upper()
        if s and re.fullmatch(r"[A-Z0-9&\-\.\^:=]{1,24}", s):
            out.append(s)
    return out


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=_json_default)


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def _clean(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else x


def atr(df: pd.DataFrame, n: int = 14) -> float:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def trading_days_after(d: pd.Timestamp, n: int) -> pd.Timestamp:
    """n-th weekday after d. NSE holidays aren't modelled: if it's a holiday, use the next trading day."""
    return pd.bdate_range(d + pd.Timedelta(days=1), periods=n)[-1]


def next_session(signal: pd.Timestamp, now_ist: datetime | None = None) -> pd.Timestamp:
    """First weekday you can still buy at the open: after the signal, and not already past.
    (A holiday like Dussehra can leave the latest bar a day behind the calendar.)"""
    now_ist = now_ist or datetime.now(IST)
    today = pd.Timestamp(now_ist.date())
    d = trading_days_after(signal, 1)
    while d < today or (d == today and (now_ist.hour, now_ist.minute) >= (9, 15)):
        d = trading_days_after(d, 1)
    return d


# ── Core: forecast a set of stocks as of their last bar ─────────────────
def forecast_universe(engine: KronosEngine, frames: dict[str, pd.DataFrame], watch: list[str]):
    """Returns (rows sorted best-first with rank/pick set, {symbol: ForecastResult})."""
    names = list(frames)
    rows, results = [], {}
    for i in range(0, len(names), CHUNK):
        part = names[i:i + CHUNK]
        log.info("  %d/%d %s", i, len(names), ", ".join(part))
        for n, f in zip(part, engine.forecast([frames[n] for n in part], horizon=HORIZON, samples=SAMPLES)):
            df = frames[n]
            c0 = float(df["close"].iloc[-1])
            summ = f.summary(c0)
            rets = f.close_paths()[:, -1] / c0 - 1
            results[n] = f
            rows.append({
                "symbol": n, "slug": slug(n), "last_close": c0, "as_of": df.index[-1].strftime("%Y-%m-%d"),
                "expected_return": summ["expected_return"], "median_return": summ["median_return"],
                "low": float(rets.min()), "high": float(rets.max()), "prob_up": summ["prob_up"],
                "spark": [round(float(x), 2) for x in df["close"].iloc[-60:]],
                "watchlist": n in watch, "nifty50": n in NIFTY50,
            })
    rows.sort(key=lambda r: r["expected_return"], reverse=True)
    picked = 0
    for k, r in enumerate(rows):
        r["rank"] = k + 1
        r["pick"] = bool(r["nifty50"] and r["expected_return"] > 0 and picked < TOP_PICKS)
        picked += r["pick"]
    return rows, results


def trade_plan(row: dict, df: pd.DataFrame) -> dict:
    c0 = row["last_close"]
    buy = next_session(pd.Timestamp(row["as_of"]))
    a = atr(df)
    return {
        "buy_date": buy.strftime("%Y-%m-%d"),
        "buy_limit": round(c0 * (1 + MAX_GAP_UP), 2),
        "sell_date": trading_days_after(buy, ledger.HOLD_DAYS).strftime("%Y-%m-%d"),
        "stop": round(max(0.01, c0 - STOP_ATR * a), 2),
        "atr": round(a, 2),
        "kronos_price": round(c0 * (1 + row["expected_return"]), 2),
    }


def forecast_payload(symbol: str, df, f, history: list[dict]) -> dict:
    c0 = float(df["close"].iloc[-1])
    closes = f.close_paths()
    hist = df.tail(HISTORY_BARS_SHOWN)
    return {
        "symbol": symbol, "yahoo": to_yahoo(symbol), "as_of": df.index[-1].strftime("%Y-%m-%d"),
        "last_close": c0, "horizon": HORIZON, "samples": SAMPLES,
        "history": [{"d": d.strftime("%Y-%m-%d"), "c": round(float(r.close), 2)} for d, r in hist.iterrows()],
        "future": [{"d": d.strftime("%Y-%m-%d"), "mean": round(float(closes[:, i].mean()), 2),
                    "lo": round(float(closes[:, i].min()), 2), "hi": round(float(closes[:, i].max()), 2)}
                   for i, d in enumerate(f.future_index)],
        "paths": [[round(float(x), 2) for x in p] for p in closes],
        "summary": f.summary(c0),
        "track": history,
    }


def _track_rows(ev: pd.DataFrame) -> list[dict]:
    keep = ["signal_date", "symbol", "close", "exp_ret", "prob_up", "rank", "source", "status", "entry_date",
            "entry", "mark_date", "mark", "days_held", "net", "market", "beat_market", "direction_right"]
    out = []
    for r in ev.to_dict("records"):
        out.append({k: _clean(r.get(k)) for k in keep})
    return out


def build() -> int:
    t0 = time.time()
    engine = KronosEngine()
    engine.load()

    watch = read_watchlist()
    symbols = list(dict.fromkeys(NIFTY50 + watch))[:MAX_SYMBOLS]
    log.info("Forecasting %d symbols (%d from watchlist)", len(symbols), len(watch))

    hist = get_many([to_yahoo(s) for s in symbols], period="3y")
    by_sym = {display_symbol(k): v for k, v in hist.items()}
    usable = {s: by_sym[s] for s in symbols if s in by_sym and len(by_sym[s]) >= DEFAULT_LOOKBACK}
    skipped = []
    for s in symbols:
        if s not in by_sym:
            skipped.append({"symbol": s, "reason": "No price data on Yahoo Finance. Check the symbol."})
        elif s not in usable:
            skipped.append({"symbol": s, "reason": f"Only {len(by_sym[s])} trading days of history; Kronos needs {DEFAULT_LOOKBACK}."})
    if not usable:
        log.error("No price data at all; refusing to publish an empty site")
        return 1

    rows, results = forecast_universe(engine, usable, watch)
    signal_date = max(r["as_of"] for r in rows)
    # Only stocks whose last bar is the latest session go in the ledger (a stale quote isn't a signal).
    fresh = [r for r in rows if r["as_of"] == signal_date]
    n = ledger.record(fresh, signal_date, "live", MODEL_ID)
    log.info("Ledger: %d forecasts recorded for %s", n, signal_date)

    # Score every past forecast against real prices.
    position = DEFAULT_CAPITAL / TOP_PICKS
    ev = ledger.evaluate(by_sym, NIFTY50, position)
    track_summary = ledger.summary(ev, position)
    cutoff = (pd.Timestamp(signal_date) - pd.Timedelta(days=TRACK_DAYS_SHOWN)).strftime("%Y-%m-%d")

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(os.path.join(OUT_DIR, "data", "f"))

    for r in rows:
        mine = ev[(ev["symbol"] == r["symbol"]) & (ev["status"] != "waiting")] if not ev.empty else ev
        mine = mine.sort_values("signal_date", ascending=False).head(15) if len(mine) else mine
        write_json(os.path.join(OUT_DIR, "data", "f", r["slug"] + ".json"),
                   forecast_payload(r["symbol"], usable[r["symbol"]], results[r["symbol"]], _track_rows(mine)))
        if r["pick"]:
            r["plan"] = trade_plan(r, usable[r["symbol"]])

    generated = datetime.now(timezone.utc).isoformat()
    write_json(os.path.join(OUT_DIR, "data", "scan.json"), {
        "generated": generated, "horizon": HORIZON, "samples": SAMPLES, "results": rows, "skipped": skipped})

    picks_ev = ev[(ev["pick"] == 1) & (ev["signal_date"] >= cutoff)] if not ev.empty else ev
    write_json(os.path.join(OUT_DIR, "data", "track.json"), {
        "generated": generated, "summary": track_summary, "hold_days": ledger.HOLD_DAYS,
        "picks": _track_rows(picks_ev.sort_values(["signal_date", "rank"], ascending=[False, True])) if len(picks_ev) else [],
    })

    # Reality check: only months not already cached are computed (usually none, ~1 min once a month).
    try:
        reality.run(engine, progress=lambda i, n, m: log.info("  reality %d/%d %s", i, n, m))
    except Exception:
        log.exception("Reality check update failed; publishing the last cached result")
    write_json(os.path.join(OUT_DIR, "data", "reality.json"), reality.summarise())

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    write_json(os.path.join(OUT_DIR, "data", "status.json"), {
        "generated": generated, "signal_date": signal_date, "model": MODEL_ID,
        "training_data_end": TRAINING_DATA_END,
        "symbols": {r["symbol"]: r["slug"] for r in rows},
        "watchlist_edit_url": f"https://github.com/{repo}/edit/main/watchlist.txt" if repo else None,
        "top_picks": TOP_PICKS, "default_capital": DEFAULT_CAPITAL, "max_gap_up": MAX_GAP_UP,
        "stop_atr": STOP_ATR, "hold_days": ledger.HOLD_DAYS,
        "cost": {"pct": ledger.round_trip_cost(1e12), "dp_per_sell": ledger.DP_PER_SELL},
        "build_seconds": round(time.time() - t0),
    })
    for name in os.listdir(WEB_DIR):
        shutil.copy(os.path.join(WEB_DIR, name), OUT_DIR)
    open(os.path.join(OUT_DIR, ".nojekyll"), "w").close()
    log.info("Site built in %.0fs: %d forecasts, %d picks, %d skipped",
             time.time() - t0, len(rows), sum(r["pick"] for r in rows), len(skipped))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "huggingface_hub", "yfinance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.exit(build())
