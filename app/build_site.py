"""
build_site.py — run Kronos once and write the whole website as static files.

GitHub Actions runs this every weekday evening after NSE closes, then publishes
site/ to GitHub Pages. Nothing runs between builds, so there's no server to host.

    python -m app.build_site            # writes ./site
    python -m http.server -d site 8000  # preview at http://localhost:8000

Steps:
  1. Forecast every stock in the Nifty 50 + watchlist.txt (20 trading days, 8 paths each).
  2. Rank them for the Scanner.
  3. Extend the Reality Check with any newly testable month (cached in data/).
  4. Copy the web app (web/) next to the JSON.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone

from . import reality
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
        json.dump(obj, f, separators=(",", ":"))


def forecast_payload(symbol: str, df, f) -> dict:
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
    }


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

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(os.path.join(OUT_DIR, "data", "f"))

    names = list(usable)
    rows = []
    for i in range(0, len(names), CHUNK):
        part = names[i:i + CHUNK]
        log.info("  %d/%d %s", i, len(names), ", ".join(part))
        fcs = engine.forecast([usable[n] for n in part], horizon=HORIZON, samples=SAMPLES)
        for n, f in zip(part, fcs):
            df = usable[n]
            payload = forecast_payload(n, df, f)
            write_json(os.path.join(OUT_DIR, "data", "f", slug(n) + ".json"), payload)
            rets = f.close_paths()[:, -1] / payload["last_close"] - 1
            rows.append({
                "symbol": n, "slug": slug(n), "last_close": payload["last_close"], "as_of": payload["as_of"],
                "expected_return": payload["summary"]["expected_return"],
                "low": float(rets.min()), "high": float(rets.max()), "prob_up": payload["summary"]["prob_up"],
                "spark": [round(float(x), 2) for x in df["close"].iloc[-60:]],
                "watchlist": n in watch, "nifty50": n in NIFTY50,
            })
    if not rows:
        log.error("No forecasts produced; refusing to publish an empty site")
        return 1
    rows.sort(key=lambda r: r["expected_return"], reverse=True)
    generated = datetime.now(timezone.utc).isoformat()
    write_json(os.path.join(OUT_DIR, "data", "scan.json"), {
        "generated": generated, "horizon": HORIZON, "samples": SAMPLES, "results": rows, "skipped": skipped})

    # Reality check: only months not already cached are computed (usually none, ~1 min once a month).
    try:
        reality.run(engine, progress=lambda i, n, m: log.info("  reality %d/%d %s", i, n, m))
    except Exception:
        log.exception("Reality check update failed; publishing the last cached result")
    write_json(os.path.join(OUT_DIR, "data", "reality.json"), reality.summarise())

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    write_json(os.path.join(OUT_DIR, "data", "status.json"), {
        "generated": generated, "model": MODEL_ID, "training_data_end": TRAINING_DATA_END,
        "symbols": {r["symbol"]: r["slug"] for r in rows},
        "watchlist_edit_url": f"https://github.com/{repo}/edit/main/watchlist.txt" if repo else None,
        "build_seconds": round(time.time() - t0),
    })
    for name in os.listdir(WEB_DIR):
        shutil.copy(os.path.join(WEB_DIR, name), OUT_DIR)
    open(os.path.join(OUT_DIR, ".nojekyll"), "w").close()
    log.info("Site built in %.0fs: %d forecasts, %d skipped", time.time() - t0, len(rows), len(skipped))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "huggingface_hub", "yfinance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.exit(build())
