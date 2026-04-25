"""FinanceDataReader-backed price fetching with simple TTL cache."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd

_PRICE_TTL = 1800  # 30 min
_HISTORY_TTL = 1800

# (key) -> (timestamp, value)
_price_cache: dict[tuple[str, ...], tuple[float, dict[str, float]]] = {}
_history_cache: dict[tuple[tuple[str, ...], int], tuple[float, pd.DataFrame]] = {}


def fetch_current_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    """Last close per ticker. Cached 30 minutes."""
    if not tickers:
        return {}
    key = tuple(sorted(tickers))
    cached = _price_cache.get(key)
    if cached and (time.time() - cached[0]) < _PRICE_TTL:
        return cached[1]

    import FinanceDataReader as fdr
    today_s = datetime.now().strftime("%Y-%m-%d")
    week_s = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    out: dict[str, float] = {}
    for t in tickers:
        try:
            df = fdr.DataReader(t, week_s, today_s)
            if not df.empty:
                out[t] = float(df["Close"].iloc[-1])
        except Exception:
            pass
    _price_cache[key] = (time.time(), out)
    return out


def fetch_price_history(tickers: tuple[str, ...], days: int = 30) -> pd.DataFrame:
    """Last `days` trading-day closes. Index=date, columns=tickers. Cached 30 min."""
    if not tickers:
        return pd.DataFrame()
    key = (tuple(sorted(tickers)), days)
    cached = _history_cache.get(key)
    if cached and (time.time() - cached[0]) < _HISTORY_TTL:
        return cached[1]

    import FinanceDataReader as fdr
    today_s = datetime.now().strftime("%Y-%m-%d")
    start_s = (datetime.now() - timedelta(days=days * 3 + 5)).strftime("%Y-%m-%d")
    series: dict[str, pd.Series] = {}
    for t in tickers:
        try:
            df = fdr.DataReader(t, start_s, today_s)
            if not df.empty:
                series[t] = df["Close"]
        except Exception:
            pass
    if not series:
        result = pd.DataFrame()
    else:
        result = pd.DataFrame(series).dropna(how="all").tail(days)
    _history_cache[key] = (time.time(), result)
    return result


def invalidate() -> None:
    _price_cache.clear()
    _history_cache.clear()
