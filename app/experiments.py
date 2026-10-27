"""
experiments.py — pre-registered research runs (see TRIALS.md). Never changes the live site.

    python -m app.experiments reversal     # is Kronos just short-term reversal? (uses Reality Check cache)
    python -m app.experiments grid         # trials 3-10: settings grid (slow; resumable)
    python -m app.experiments report       # print the grid results table
    python -m app.experiments fresh        # trials 14-16: Nifty Next 150 fresh-stock test (slow; resumable)

Rules (same as the Reality Check): month-end rebalances from Jul 2024, only data up to each
date, buy at the next open, sell at the open `horizon` trading days later, full Zerodha
charges via ledger.round_trip_cost. Results cache in data/experiments/ so runs can resume.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time

import numpy as np
import pandas as pd

from . import engine as E
from . import reality
from .data import NIFTY50, display_symbol, get_many, to_yahoo
from .ledger import round_trip_cost

log = logging.getLogger("kronos.experiments")

# ── Config (pre-registered in TRIALS.md, trials 3-10) ────────────────────
GRID = [(T, S, L, H) for T in (1.0, 0.6) for S in (8, 10) for (L, H) in ((256, 20), (90, 10))]
BASELINE = (1.0, 8, 256, 20)          # what the live site runs
TOP_N = 10
POSITION_RUPEES = 10_000
NW_LAGS = 2
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "experiments")


# ── Shared helpers ───────────────────────────────────────────────────────
def load_prices() -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    hist = {display_symbol(k): v for k, v in get_many([to_yahoo(s) for s in NIFTY50], period="max").items()}
    if len(hist) < 40:
        raise RuntimeError(f"Only {len(hist)} Nifty 50 price histories downloaded; refusing to run")
    closes = pd.DataFrame({k: v["close"] for k, v in hist.items()}).sort_index()
    return hist, closes.dropna(how="all").index


def rebalance_dates(cal: pd.DatetimeIndex, max_h: int = 20) -> list[pd.Timestamp]:
    """Same month-ends for every combo: those with a full max_h-day next-open hold available."""
    return [d for d in reality._month_end_dates(cal) if cal.get_loc(d) + 1 + max_h < len(cal)]


def open_to_open(df: pd.DataFrame, t: pd.Timestamp, h: int) -> float | None:
    pos = df.index.searchsorted(t, side="right")
    if pos + h >= len(df):
        return None
    a, b = float(df["open"].iloc[pos]), float(df["open"].iloc[pos + h])
    return b / a - 1 if a > 0 else None


def spearman(a, b) -> float:
    return reality._spearman(np.asarray(a, float), np.asarray(b, float))


def newey_west_t(x: list[float], lags: int = NW_LAGS) -> float:
    x = np.asarray([v for v in x if v is not None and not math.isnan(v)], float)
    n = len(x)
    if n < 6:
        return float("nan")
    d = x - x.mean()
    var = d @ d / n
    for k in range(1, lags + 1):
        w = 1 - k / (lags + 1)
        var += 2 * w * (d[k:] @ d[:-k]) / n
    return float(x.mean() / math.sqrt(var / n)) if var > 0 else float("nan")


def portfolio_stats(rows: pd.DataFrame, score: str, ret: str) -> dict:
    """Monthly RankIC + top-N equal-weight net excess vs the equal-weight universe."""
    cost = round_trip_cost(POSITION_RUPEES)
    ics, excess, net = [], [], []
    prev: set[str] = set()
    for _, g in rows.dropna(subset=[score, ret]).groupby("date", sort=True):
        ics.append(spearman(g[score], g[ret]))
        top = g.nlargest(TOP_N, score)
        names = set(top["symbol"])
        turnover = len(names - prev) / TOP_N
        prev = names
        r = float(top[ret].mean()) - turnover * cost
        net.append(r)
        excess.append(r - float(g[ret].mean()))
    ic = np.array([v for v in ics if not math.isnan(v)])
    ex = np.array(excess)
    return {
        "months": int(len(ic)),
        "rank_ic": float(ic.mean()) if len(ic) else float("nan"),
        "t_nw": newey_west_t(list(ic)),
        "ic_positive": float((ic > 0).mean()) if len(ic) else float("nan"),
        "net_excess_per_rebalance": float(ex.mean()) if len(ex) else float("nan"),
        "excess_positive": float((ex > 0).mean()) if len(ex) else float("nan"),
        "top3_share_of_excess": float(np.sort(ex)[-3:].sum() / ex.sum()) if len(ex) and ex.sum() > 0 else None,
        "total_net_top10": float(np.prod([1 + r for r in net]) - 1) if net else float("nan"),
    }


# ── Experiment 2: reversal decomposition ─────────────────────────────────
def factor_values(df: pd.DataFrame, t: pd.Timestamp) -> dict | None:
    past = df["close"].loc[:t]
    if len(past) < 253 or past.index[-1] != t:
        return None
    c = past.to_numpy()
    rets = np.diff(np.log(c[-61:]))
    return {
        "rev5": -(c[-1] / c[-6] - 1),          # higher = fell more = reversal buy
        "rev21": -(c[-1] / c[-22] - 1),
        "mom12_1": c[-22] / c[-253] - 1,
        "vol60": float(rets.std()),
    }


def run_reversal() -> dict:
    cache = reality.load_cache()
    rows = pd.DataFrame(cache["rows"])
    if rows.empty or "realised_oo" not in rows:
        raise RuntimeError("Reality Check cache missing next-open returns; run the site build first")
    hist, _ = load_prices()
    feats = []
    for r in rows.itertuples(index=False):
        df = hist.get(r.symbol)
        f = factor_values(df, pd.Timestamp(r.date)) if df is not None else None
        feats.append(f or {})
    rows = pd.concat([rows.reset_index(drop=True), pd.DataFrame(feats)], axis=1)
    rows = rows.dropna(subset=["pred", "realised_oo", "rev5", "rev21", "mom12_1", "vol60"])

    resid_ics, r2s, raw_ics = [], [], []
    base = {k: [] for k in ("rev5", "rev21", "mom12_1", "vol60")}
    for _, g in rows.groupby("date", sort=True):
        rk = lambda s: (s.rank() - 1) / (len(s) - 1) - 0.5
        y = rk(g["pred"]).to_numpy()
        X = np.column_stack([np.ones(len(g))] + [rk(g[k]).to_numpy() for k in base])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        r2s.append(1 - (resid @ resid) / (y @ y) if y @ y > 0 else np.nan)
        resid_ics.append(spearman(resid, g["realised_oo"]))
        raw_ics.append(spearman(g["pred"], g["realised_oo"]))
        for k in base:
            base[k].append(spearman(g[k], g["realised_oo"]))
    out = {
        "months": len(raw_ics),
        "kronos_rank_ic": float(np.nanmean(raw_ics)), "kronos_t_nw": newey_west_t(raw_ics),
        "share_explained_r2": float(np.nanmean(r2s)),
        "residual_rank_ic": float(np.nanmean(resid_ics)), "residual_t_nw": newey_west_t(resid_ics),
        "baselines": {k: {"rank_ic": float(np.nanmean(v)), "t_nw": newey_west_t(v)} for k, v in base.items()},
        "corr_pred_vs_rev21": float(rows.groupby("date").apply(lambda g: spearman(g["pred"], g["rev21"])).mean()),
        "corr_pred_vs_rev5": float(rows.groupby("date").apply(lambda g: spearman(g["pred"], g["rev5"])).mean()),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "reversal_check.json"), "w") as f:
        json.dump(out, f, indent=1)
    return out


# ── Experiment 1: settings grid ──────────────────────────────────────────
def combo_file(c) -> str:
    T, S, L, H = c
    return os.path.join(OUT_DIR, f"grid_T{T}_S{S}_L{L}_H{H}.json")


def run_grid(only: list[tuple] | None = None) -> None:
    hist, cal = load_prices()
    dates = rebalance_dates(cal)
    log.info("Grid: %d rebalance dates %s -> %s", len(dates), dates[0].date(), dates[-1].date())
    eng = E.KronosEngine()
    eng.load()
    os.makedirs(OUT_DIR, exist_ok=True)
    for c in (only or GRID):
        T, S, L, H = c
        path = combo_file(c)
        cache = json.load(open(path)) if os.path.exists(path) else {"combo": list(c), "rows": [], "done": []}
        E.TEMPERATURE = T                       # engine reads this at sampling time
        for t in dates:
            ds = t.strftime("%Y-%m-%d")
            if ds in cache["done"]:
                continue
            t0 = time.time()
            frames, names = [], []
            for s, df in hist.items():
                past = df.loc[:t]
                if len(past) >= max(L, 256) + 1 and past.index[-1] == t:   # same stock set for every combo
                    frames.append(past)
                    names.append(s)
            fcs = eng.forecast(frames, horizon=H, samples=S, lookback=L)
            for s, past, f in zip(names, frames, fcs):
                c0 = float(past["close"].iloc[-1])
                cache["rows"].append({"date": ds, "symbol": s, "pred": f.summary(c0)["expected_return"],
                                      "realised_oo": open_to_open(hist[s], t, H)})
            cache["done"].append(ds)
            tmp = path + ".tmp"
            json.dump(cache, open(tmp, "w"))
            os.replace(tmp, path)
            log.info("T=%s S=%s L=%s H=%s  %s: %d stocks (%.0fs)", T, S, L, H, ds, len(names), time.time() - t0)
    E.TEMPERATURE = 1.0


def grid_report() -> list[dict]:
    out = []
    for c in GRID:
        path = combo_file(c)
        if not os.path.exists(path):
            continue
        cache = json.load(open(path))
        st = portfolio_stats(pd.DataFrame(cache["rows"]), "pred", "realised_oo")
        out.append({"T": c[0], "samples": c[1], "lookback": c[2], "horizon": c[3],
                    "complete": len(cache["done"]), **st})
    return out


# ── Experiment 3: fresh-stock test on the Nifty Next 150 (trials 14-16) ──
FRESH_FILE = os.path.join(OUT_DIR, "fresh_next150.json")
FRESH_UNIVERSE = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "nifty200_2026-10-27.csv")
FRESH_SLIPPAGE = 0.0010          # 10 bps a side for smaller stocks (pre-registered)


def next150() -> list[str]:
    syms = pd.read_csv(FRESH_UNIVERSE)["Symbol"].str.strip().tolist()
    return [s for s in syms if s not in set(NIFTY50)]


def run_fresh() -> None:
    T, S, L, H = BASELINE
    names_all = next150()
    hist = {display_symbol(k): v for k, v in get_many([to_yahoo(s) for s in names_all], period="max").items()}
    if len(hist) < 120:
        raise RuntimeError(f"Only {len(hist)} of 150 histories downloaded; refusing to run")
    base_cache = json.load(open(combo_file(BASELINE)))
    dates = [pd.Timestamp(d) for d in sorted(base_cache["done"])]       # identical 26 dates
    cache = json.load(open(FRESH_FILE)) if os.path.exists(FRESH_FILE) else {"rows": [], "done": []}
    eng = E.KronosEngine()
    eng.load()
    E.TEMPERATURE = T
    for t in dates:
        ds = t.strftime("%Y-%m-%d")
        if ds in cache["done"]:
            continue
        t0 = time.time()
        frames, names = [], []
        for s, df in hist.items():
            past = df.loc[:t]
            if len(past) >= 257 and past.index[-1] == t:
                frames.append(past)
                names.append(s)
        fcs = eng.forecast(frames, horizon=H, samples=S, lookback=L)
        for s, past, f in zip(names, frames, fcs):
            c0 = float(past["close"].iloc[-1])
            fv = factor_values(hist[s], t) or {}
            cache["rows"].append({"date": ds, "symbol": s, "pred": f.summary(c0)["expected_return"],
                                  "rev21": fv.get("rev21"), "realised_oo": open_to_open(hist[s], t, H)})
        cache["done"].append(ds)
        tmp = FRESH_FILE + ".tmp"
        json.dump(cache, open(tmp, "w"))
        os.replace(tmp, FRESH_FILE)
        log.info("fresh %s: %d stocks (%.0fs)", ds, len(names), time.time() - t0)


def fresh_report() -> dict:
    from . import ledger
    d = pd.DataFrame(json.load(open(FRESH_FILE))["rows"]).dropna(subset=["pred", "rev21", "realised_oo"])
    pr = lambda g, c: g[c].rank(pct=True)
    d["blend"] = d.groupby("date", group_keys=False).apply(lambda g: (pr(g, "pred") + pr(g, "rev21")) / 2)
    old = ledger.SLIPPAGE
    ledger.SLIPPAGE = FRESH_SLIPPAGE
    out = {}
    try:
        for name, col in (("A_kronos", "pred"), ("B_rev21", "rev21"), ("C_blend", "blend")):
            st = portfolio_stats(d, col, "realised_oo")
            years = {}
            for y, g in d.assign(year=d.date.str[:4]).groupby("year"):
                ics = [spearman(h[col], h["realised_oo"]) for _, h in g.groupby("date")]
                years[y] = float(np.nanmean(ics))
            st["ic_by_year"] = years
            st["passes"] = bool(st["rank_ic"] >= 0.03 and st["t_nw"] >= 2.0 and st["net_excess_per_rebalance"] > 0
                                and sum(v > 0 for v in years.values()) >= 2)
            out[name] = st
    finally:
        ledger.SLIPPAGE = old
    out["stocks_per_month"] = float(d.groupby("date").size().median())
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "huggingface_hub", "yfinance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "reversal":
        print(json.dumps(run_reversal(), indent=1))
    elif cmd == "fresh":
        run_fresh()
        print(json.dumps(fresh_report(), indent=1))
    elif cmd == "fresh-report":
        print(json.dumps(fresh_report(), indent=1))
    elif cmd == "grid":
        run_grid()
        print(json.dumps(grid_report(), indent=1))
    else:
        print(json.dumps(grid_report(), indent=1))
