"""
reality.py — does Kronos actually predict NSE returns? An honest walk-forward test.

Rules (no peeking):
  * Kronos's training data ends June 2024, so every forecast here is for a period the
    model never saw. The first rebalance date is the last trading day of July 2024.
  * Each month-end t, every stock is forecast using only candles up to t.
  * Trade the way a person running this site could: forecast after the close on t, buy at
    the open of t+1, sell at the open 20 trading days later (realised_oo). Older cache rows
    also keep the close-to-close return (realised) for reference.
  * The strategy holds the 10 stocks with the highest Kronos expected return, equal weight,
    and is compared with (a) the equal-weight universe and (b) plain 12-1 month momentum.
  * Costs: the full Zerodha delivery stack (STT both sides, stamp duty, exchange + GST, the
    ~₹18 demat charge per sell at a ₹10,000 position, 5 bps slippage each side), about 0.5%
    round trip, charged on every name that changes.

Caveat: the universe is today's Nifty 50, so stocks that dropped out are missing
(survivorship bias). This flatters every strategy equally, including the benchmark.

Run directly to (re)build the cache:  python -m app.reality
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .data import UNIVERSES, display_symbol, get_many, to_yahoo
from .ledger import round_trip_cost
from .engine import DEFAULT_LOOKBACK, TRAINING_DATA_END, KronosEngine

log = logging.getLogger("kronos.reality")

# ── Config ───────────────────────────────────────────────────────────────
HORIZON = 20
SAMPLES = 5
TOP_N = 10
POSITION_RUPEES = 10_000            # ₹1L across 10 names; sets the per-trade demat charge
COST_ROUND_TRIP = round_trip_cost(POSITION_RUPEES)
FIRST_REBALANCE_MONTH = "2024-07"   # first month-end strictly after the training cutoff
MOM_LOOKBACK, MOM_SKIP = 252, 21     # 12-1 month momentum baseline
PASS_RANK_IC, PASS_T = 0.03, 2.0     # gate: mean RankIC and its t-stat
DATA_DIR = os.environ.get("KRONOS_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
CACHE_FILE = os.path.join(DATA_DIR, "reality_check.json")


def _month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(index, index=index)
    ends = s.groupby(index.to_period("M")).max()
    return [d for d in ends if d.to_period("M") >= pd.Period(FIRST_REBALANCE_MONTH)]


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 5:
        return float("nan")
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"universe": "nifty50", "periods": [], "rows": []}


def _save(cache: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, CACHE_FILE)


def _open_to_open(df: pd.DataFrame, signal_date: str) -> float | None:
    """Buy at the open after signal_date, sell at the open HORIZON trading days later."""
    pos = df.index.searchsorted(pd.Timestamp(signal_date), side="right")
    if pos + HORIZON >= len(df):
        return None
    entry, exit_ = float(df["open"].iloc[pos]), float(df["open"].iloc[pos + HORIZON])
    return exit_ / entry - 1 if entry > 0 else None


def reprice(cache: dict, hist: dict[str, pd.DataFrame]) -> int:
    """Fill realised_oo (next-open entry and exit) for cached rows that don't have it yet."""
    by_sym = {display_symbol(k): v for k, v in hist.items()}
    n = 0
    for r in cache["rows"]:
        if r.get("realised_oo") is None and r["symbol"] in by_sym:
            v = _open_to_open(by_sym[r["symbol"]], r["date"])
            if v is not None:
                r["realised_oo"] = v
                n += 1
    return n


def run(engine: KronosEngine, universe: str = "nifty50", progress=None,
        stop: threading.Event | None = None) -> dict:
    """Compute any month-ends not yet in the cache. Safe to stop and resume."""
    cache = load_cache()
    done = {p["date"] for p in cache["periods"]}
    symbols = UNIVERSES[universe]["symbols"]
    hist = get_many([to_yahoo(s) for s in symbols], period="max")
    if not hist:
        raise RuntimeError("No price data available")
    closes = pd.DataFrame({k: v["close"] for k, v in hist.items()}).sort_index()
    cal = closes.dropna(how="all").index
    todo = [d for d in _month_end_dates(cal)
            if d.strftime("%Y-%m-%d") not in done and cal.get_loc(d) + HORIZON < len(cal)]
    log.info("Reality check: %d periods to compute", len(todo))
    if reprice(cache, hist):
        _save(cache)

    for k, t in enumerate(todo):
        if stop is not None and stop.is_set():
            break
        i_t = cal.get_loc(t)
        t_exit = cal[i_t + HORIZON]
        frames, names = [], []
        for ysym, df in hist.items():
            past = df.loc[:t]
            if len(past) < DEFAULT_LOOKBACK + 1 or past.index[-1] != t:
                continue
            if pd.isna(closes.at[t_exit, ysym]):
                continue
            frames.append(past)
            names.append(ysym)
        if progress:
            progress(k, len(todo), f"Forecasting {t:%b %Y} ({len(names)} stocks)")
        if len(names) < 20:
            continue
        fc = engine.forecast(frames, horizon=HORIZON, samples=SAMPLES)

        rows = []
        for ysym, past, f in zip(names, frames, fc):
            c0 = float(past["close"].iloc[-1])
            summ = f.summary(c0)
            realised = float(closes.at[t_exit, ysym] / c0 - 1)
            sample_rets = f.close_paths()[:, -1] / c0 - 1
            mom = np.nan
            if i_t - MOM_LOOKBACK >= 0:
                a = closes[ysym].iloc[i_t - MOM_LOOKBACK]
                b = closes[ysym].iloc[i_t - MOM_SKIP]
                if pd.notna(a) and pd.notna(b) and a > 0:
                    mom = float(b / a - 1)
            rows.append({
                "date": t.strftime("%Y-%m-%d"), "symbol": display_symbol(ysym),
                "pred": summ["expected_return"], "lo": float(sample_rets.min()),
                "hi": float(sample_rets.max()), "prob_up": summ["prob_up"],
                "realised": realised, "momentum": mom,
            })
        cache["rows"].extend(rows)
        cache["periods"].append({"date": t.strftime("%Y-%m-%d"), "exit": t_exit.strftime("%Y-%m-%d"),
                                 "n": len(rows)})
        cache["periods"].sort(key=lambda p: p["date"])
        cache["updated"] = datetime.now(timezone.utc).isoformat()
        reprice(cache, hist)
        _save(cache)   # checkpoint every month so a restart loses at most one period
    if progress:
        progress(len(todo), len(todo), "Done")
    return cache


def summarise(cache: dict | None = None) -> dict:
    """Turn cached forecast rows into the numbers shown on the Reality Check page."""
    cache = cache or load_cache()
    rows = pd.DataFrame(cache.get("rows", []))
    if rows.empty:
        return {"ready": False, "periods": 0}
    # Tradeable returns (next-open in, open out) once they're available; close-to-close otherwise.
    tradeable = "realised_oo" in rows and rows["realised_oo"].notna().mean() > 0.9
    if tradeable:
        rows["realised"] = rows["realised_oo"]
    out_periods = []
    prev_k, prev_m = set(), set()
    eq_k = eq_m = eq_b = 1.0
    for date, g in rows.groupby("date", sort=True):
        g = g.dropna(subset=["pred", "realised"])
        ic = _spearman(g["pred"].to_numpy(), g["realised"].to_numpy())
        gm = g.dropna(subset=["momentum"])
        mic = _spearman(gm["momentum"].to_numpy(), gm["realised"].to_numpy()) if len(gm) >= 5 else float("nan")

        top_k = g.nlargest(TOP_N, "pred")
        top_m = gm.nlargest(TOP_N, "momentum") if len(gm) >= TOP_N else gm
        set_k, set_m = set(top_k["symbol"]), set(top_m["symbol"])
        turn_k = len(set_k - prev_k) / TOP_N
        turn_m = len(set_m - prev_m) / TOP_N if len(top_m) else 0.0
        prev_k, prev_m = set_k, set_m
        r_k = float(top_k["realised"].mean()) - turn_k * COST_ROUND_TRIP
        r_m = float(top_m["realised"].mean()) - turn_m * COST_ROUND_TRIP if len(top_m) else 0.0
        r_b = float(g["realised"].mean())
        eq_k *= 1 + r_k
        eq_m *= 1 + r_m
        eq_b *= 1 + r_b
        inside = ((g["realised"] >= g["lo"]) & (g["realised"] <= g["hi"])).mean()
        hit = (np.sign(g["pred"]) == np.sign(g["realised"])).mean()
        out_periods.append({
            "date": date, "n": int(len(g)), "rank_ic": _nz(ic), "mom_rank_ic": _nz(mic),
            "kronos_ret": r_k, "mom_ret": r_m, "bench_ret": r_b,
            "kronos_equity": eq_k, "mom_equity": eq_m, "bench_equity": eq_b,
            "band_coverage": float(inside), "direction_hit": float(hit),
            "top": top_k["symbol"].tolist(),
        })
    p = pd.DataFrame(out_periods)
    ics = p["rank_ic"].dropna()
    n = len(ics)
    mean_ic = float(ics.mean()) if n else float("nan")
    t_stat = float(mean_ic / (ics.std(ddof=1) / math.sqrt(n))) if n > 2 and ics.std(ddof=1) > 0 else float("nan")
    mics = p["mom_rank_ic"].dropna()
    excess = p["kronos_ret"] - p["bench_ret"]
    passed = bool(n >= 12 and mean_ic >= PASS_RANK_IC and t_stat >= PASS_T and excess.mean() > 0)
    return {
        "ready": True,
        "updated": cache.get("updated"),
        "training_data_end": TRAINING_DATA_END,
        "periods": n,
        "first": p["date"].iloc[0], "last": p["date"].iloc[-1],
        "stocks_per_period": int(p["n"].median()),
        "horizon_days": HORIZON, "samples": SAMPLES, "top_n": TOP_N, "cost_round_trip": COST_ROUND_TRIP,
        "entry": "next_open" if tradeable else "close", "position_rupees": POSITION_RUPEES,
        "mean_rank_ic": mean_ic, "t_stat": t_stat,
        "pct_months_ic_positive": float((ics > 0).mean()) if n else float("nan"),
        "mom_mean_rank_ic": float(mics.mean()) if len(mics) else float("nan"),
        "kronos_total": float(p["kronos_equity"].iloc[-1] - 1),
        "mom_total": float(p["mom_equity"].iloc[-1] - 1),
        "bench_total": float(p["bench_equity"].iloc[-1] - 1),
        "avg_monthly_excess": float(excess.mean()),
        "band_coverage": float(p["band_coverage"].mean()),
        # With S exchangeable samples, a calibrated model lands inside [min, max] (S-1)/(S+1) of the time.
        "band_target": (SAMPLES - 1) / (SAMPLES + 1),
        "direction_hit": float(p["direction_hit"].mean()),
        "gate": {"rank_ic_min": PASS_RANK_IC, "t_min": PASS_T, "passed": passed},
        "table": out_periods,
    }


def _nz(x: float):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    eng = KronosEngine()
    run(eng, progress=lambda i, n, msg: log.info("[%d/%d] %s", i, n, msg))
    s = summarise()
    print(json.dumps({k: v for k, v in s.items() if k != "table"}, indent=2))
