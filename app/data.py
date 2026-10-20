"""
data.py — daily candles from Yahoo Finance, with a small cache.

Prices are split/dividend adjusted (auto_adjust=True), so backtests aren't
broken by corporate actions. NSE symbols get ".NS" appended automatically.
"""
from __future__ import annotations

import logging
import threading
import time

import pandas as pd
import yfinance as yf

log = logging.getLogger("kronos.data")

CACHE_TTL_SEC = 3 * 3600
MAX_RETRIES = 3

# NIFTY 50 as of the 30 Sep 2026 rebalance (BSE in, Wipro out). Source: niftyindices.com
# press release 10 Aug 2026 + prior constituent list. Best-effort: check NSE for the official list.
NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE",
    "BAJAJFINSV", "BEL", "BHARTIARTL", "BSE", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO",
    "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND",
    "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN", "SUNPHARMA", "TCS",
    "TATACONSUM", "TMPV", "TATASTEEL", "TECHM", "TITAN", "TRENT", "ULTRACEMCO",
]

UNIVERSES = {
    "nifty50": {"label": "Nifty 50", "symbols": NIFTY50},
}

_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_lock = threading.Lock()


def to_yahoo(symbol: str) -> str:
    """'reliance' -> 'RELIANCE.NS'; leaves explicit suffixes / indices / US tickers alone."""
    s = symbol.strip().upper()
    if not s:
        raise ValueError("empty symbol")
    if s.startswith("^") or "." in s or s.endswith("=X") or s.endswith("-USD"):
        return s
    if s.startswith("US:"):          # 'US:AAPL' -> 'AAPL'
        return s[3:]
    return s + ".NS"


def display_symbol(yahoo: str) -> str:
    return yahoo[:-3] if yahoo.endswith(".NS") else yahoo


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[cols].dropna(subset=["open", "high", "low", "close"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if "volume" not in df:
        df["volume"] = 0.0
    df["volume"] = df["volume"].fillna(0.0).astype(float)
    return df


def get_history(symbol: str, period: str = "5y") -> pd.DataFrame:
    """Daily OHLCV for one symbol. Raises ValueError if Yahoo has nothing."""
    ysym = to_yahoo(symbol)
    key = f"{ysym}|{period}"
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_SEC:
            return hit[1].copy()
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            raw = yf.download(ysym, period=period, interval="1d", auto_adjust=True,
                              progress=False, threads=False, multi_level_index=False)
            df = _clean(raw)
            if df.empty:
                raise ValueError(f"No data for {ysym}")
            with _lock:
                _cache[key] = (time.time(), df)
            return df.copy()
        except Exception as e:  # network, rate limit, delisted
            last_err = e
            log.warning("fetch %s failed (attempt %d): %s", ysym, attempt + 1, e)
            time.sleep(1.5 * (attempt + 1))
    raise ValueError(f"Could not fetch {ysym}: {last_err}")


def get_many(symbols: list[str], period: str = "5y") -> dict[str, pd.DataFrame]:
    """Fetch several symbols; silently skips ones Yahoo can't serve (caller reports them)."""
    out = {}
    for s in symbols:
        try:
            out[s] = get_history(s, period)
        except ValueError:
            pass
    return out
