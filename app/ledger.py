"""
ledger.py — remember every forecast and score it once its 20 days are up.

data/ledger/forecasts.csv gets one row per stock per evening run. Rows are never
edited after they are written; only a re-run for the same signal date replaces
that date's rows. That makes the file an honest, append-only track record.

Scoring (same rule the trade plan tells you to follow):
  entry = open of the first trading day after the signal date
  exit  = open of the 21st trading day after the signal date (a 20-day hold)
  net   = gross - estimated Zerodha delivery charges at the planned position size
  market = the average Nifty 50 stock over the same open-to-open window
Open picks are marked to the latest close.
"""
from __future__ import annotations

import csv
import logging
import os
from functools import lru_cache

import numpy as np
import pandas as pd

log = logging.getLogger("kronos.ledger")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LEDGER_FILE = os.path.join(ROOT, "data", "ledger", "forecasts.csv")
HOLD_DAYS = 20
COLUMNS = ["signal_date", "symbol", "close", "exp_ret", "med_ret", "lo_ret", "hi_ret", "prob_up",
           "rank", "pick", "universe", "source", "model"]

# ── Cost model: Zerodha equity delivery (Oct 2026 published rates) ───────
STT = 0.001            # 0.1% on buy and on sell
STAMP_BUY = 0.00015    # 0.015% on buy
EXCH = 0.0000307       # NSE transaction charge, each side
SEBI_FEE = 0.000001    # ₹10 per crore, each side
GST = 0.18             # on exchange + SEBI charges (brokerage is ₹0 for delivery)
DP_PER_SELL = 15.34 * 1.18   # ₹ per scrip per sell day, incl. GST
SLIPPAGE = 0.0005      # 5 bps each side, a conservative allowance for Nifty 50 names


def round_trip_cost(position_rupees: float) -> float:
    """Total charges for buying and later selling one position, as a fraction of its value."""
    pct = 2 * STT + STAMP_BUY + 2 * (EXCH + SEBI_FEE) * (1 + GST) + 2 * SLIPPAGE
    return pct + DP_PER_SELL / max(position_rupees, 1.0)


# ── Writing ──────────────────────────────────────────────────────────────
def load() -> pd.DataFrame:
    if not os.path.exists(LEDGER_FILE):
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(LEDGER_FILE, dtype={"symbol": str, "signal_date": str})


def record(rows: list[dict], signal_date: str, source: str, model: str) -> int:
    """Replace this signal date's rows with `rows` (idempotent re-runs). Returns rows written."""
    df = load()
    if source == "backfill" and (df["signal_date"] == signal_date).any():
        return 0   # never overwrite a live record with a backfill
    df = df[df["signal_date"] != signal_date]
    new = pd.DataFrame([{
        "signal_date": signal_date, "symbol": r["symbol"], "close": round(r["last_close"], 2),
        "exp_ret": round(r["expected_return"], 5), "med_ret": round(r["median_return"], 5),
        "lo_ret": round(r["low"], 5), "hi_ret": round(r["high"], 5), "prob_up": round(r["prob_up"], 3),
        "rank": r["rank"], "pick": int(r["pick"]), "universe": "nifty50" if r["nifty50"] else "watchlist",
        "source": source, "model": model,
    } for r in rows], columns=COLUMNS)
    out = pd.concat([df, new], ignore_index=True).sort_values(["signal_date", "rank"])
    os.makedirs(os.path.dirname(LEDGER_FILE), exist_ok=True)
    out.to_csv(LEDGER_FILE, index=False, quoting=csv.QUOTE_MINIMAL)
    return len(new)


# ── Scoring ──────────────────────────────────────────────────────────────
def _window(df: pd.DataFrame, signal_date: str):
    """(entry_date, entry_open, exit_date, exit_open or None, mark_date, mark_price, days_held)."""
    idx = df.index
    pos = idx.searchsorted(pd.Timestamp(signal_date), side="right")   # first bar after signal
    if pos >= len(idx):
        return None
    entry_d, entry = idx[pos], float(df["open"].iloc[pos])
    exit_pos = pos + HOLD_DAYS
    if exit_pos < len(idx):
        return entry_d, entry, idx[exit_pos], float(df["open"].iloc[exit_pos]), idx[exit_pos], \
            float(df["open"].iloc[exit_pos]), HOLD_DAYS
    return entry_d, entry, None, None, idx[-1], float(df["close"].iloc[-1]), len(idx) - 1 - pos


def evaluate(prices: dict[str, pd.DataFrame], nifty50: list[str], position_rupees: float) -> pd.DataFrame:
    """Score every ledger row that has at least an entry bar. Returns one row per forecast."""
    led = load()
    if led.empty:
        return led
    cost = round_trip_cost(position_rupees)

    @lru_cache(maxsize=None)
    def market(signal_date: str, mark_date: str, closed: bool) -> float:
        rets = []
        for s in nifty50:
            df = prices.get(s)
            if df is None:
                continue
            w = _window(df, signal_date)
            if not w:
                continue
            md = pd.Timestamp(mark_date)
            if md not in df.index:
                continue
            end = float(df.at[md, "open"] if closed else df.at[md, "close"])
            rets.append(end / w[1] - 1)
        return float(np.mean(rets)) if rets else float("nan")

    out = []
    for r in led.itertuples(index=False):
        df = prices.get(r.symbol)
        if df is None:
            continue
        w = _window(df, r.signal_date)
        if not w:
            out.append({**r._asdict(), "status": "waiting"})
            continue
        entry_d, entry, exit_d, exit_px, mark_d, mark_px, held = w
        closed = exit_d is not None
        gross = mark_px / entry - 1
        mkt = market(r.signal_date, mark_d.strftime("%Y-%m-%d"), closed)
        out.append({
            **r._asdict(), "status": "closed" if closed else "open",
            "entry_date": entry_d.strftime("%Y-%m-%d"), "entry": round(entry, 2),
            "mark_date": mark_d.strftime("%Y-%m-%d"), "mark": round(mark_px, 2), "days_held": held,
            "gross": gross, "net": gross - cost,   # for open picks: if sold at today's close
            "market": mkt, "beat_market": bool(gross - cost > mkt) if closed and not np.isnan(mkt) else None,
            "direction_right": bool(np.sign(r.exp_ret) == np.sign(gross)) if closed else None,
            "in_range": bool(r.lo_ret <= (mark_px / r.close - 1) <= r.hi_ret) if closed else None,
        })
    return pd.DataFrame(out)


def summary(ev: pd.DataFrame, position_rupees: float) -> dict:
    """Numbers for the Track record page."""
    if ev.empty or "status" not in ev:
        return {"picks_closed": 0}
    picks = ev[(ev["pick"] == 1) & (ev["status"] == "closed")]
    allc = ev[ev["status"] == "closed"]

    def block(p: pd.DataFrame) -> dict:
        if p.empty:
            return {"n": 0}
        return {
            "n": int(len(p)),
            "profitable": float((p["net"] > 0).mean()),
            "beat_market": float(p["beat_market"].astype(bool).mean()),
            "avg_net": float(p["net"].mean()),
            "avg_market": float(p["market"].mean()),
            "rupees": float((p["net"] * position_rupees).sum()),
        }

    return {
        "position_rupees": position_rupees,
        "round_trip_cost": round_trip_cost(position_rupees),
        "picks": block(picks),
        "picks_live": block(picks[picks["source"] == "live"]),
        "picks_backfill": block(picks[picks["source"] == "backfill"]),
        "all_closed": int(len(allc)),
        "direction_right": float(allc["direction_right"].astype(bool).mean()) if len(allc) else None,
        "in_range": float(allc["in_range"].astype(bool).mean()) if len(allc) else None,
        "first_signal": ev["signal_date"].min(),
        "last_signal": ev["signal_date"].max(),
    }
