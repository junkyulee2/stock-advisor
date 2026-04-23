"""Data collection layer.

Primary: FinanceDataReader (prices, listings, KOSPI).
Secondary: Naver Finance crawling (foreign/institution flows, fundamentals).

All data is 'as of T-1' (previous trading day) to prevent lookahead bias.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Iterable, Optional

import FinanceDataReader as fdr
import pandas as pd
import requests
from bs4 import BeautifulSoup

from .cache import disk_cached
from .utils import setup_logger

logger = setup_logger(__name__)

# Cache TTLs (seconds)
TTL_DAY = 24 * 3600
TTL_WEEK = 7 * 24 * 3600

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


# ============================================================
# Universe & Prices (FinanceDataReader)
# ============================================================

_listing_cached: dict = {}


def _get_listing() -> pd.DataFrame:
    """Cached KRX listing fetch. Returns full KOSPI+KOSDAQ."""
    if "df" not in _listing_cached:
        df = fdr.StockListing("KRX")
        # Normalize
        df = df.rename(columns={
            "Code": "ticker", "Name": "name", "Market": "market",
            "Close": "close", "Marcap": "market_cap",
            "Amount": "trading_value", "Volume": "volume",
        })
        _listing_cached["df"] = df
    return _listing_cached["df"]


def get_universe(as_of: str, markets: Iterable[str], top_n: int) -> pd.DataFrame:
    """Top-N by market cap across given markets.

    Note: `as_of` is used conceptually (FDR returns latest snapshot).
    For strict historical universe, would need historical listings.
    """
    df = _get_listing()
    markets_upper = [m.upper() for m in markets]
    df = df[df["market"].isin(markets_upper)].copy()
    df = df.sort_values("market_cap", ascending=False).head(top_n)
    df = df.reset_index(drop=True)
    return df[["ticker", "name", "market", "market_cap", "close", "trading_value"]]


@disk_cached(lambda ticker, start, end: f"ohlcv_{ticker}_{start}_{end}", ttl=TTL_DAY)
def get_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Daily OHLCV via FDR. start/end in YYYYMMDD. Cached 1 day."""
    start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
    df = fdr.DataReader(ticker, start_fmt, end_fmt)
    if df.empty:
        return df
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume", "Change": "change_pct",
    })
    return df


@disk_cached(lambda start, end: f"kospi_{start}_{end}", ttl=TTL_DAY)
def get_kospi_ohlcv(start: str, end: str) -> pd.DataFrame:
    """KOSPI index OHLCV (code KS11 in FDR). Cached 1 day."""
    start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
    df = fdr.DataReader("KS11", start_fmt, end_fmt)
    if df.empty:
        return df
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume", "Change": "change_pct",
    })
    return df


def date_range_for_lookback(end_date: str, lookback_days: int) -> tuple[str, str]:
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    start_dt = end_dt - timedelta(days=int(lookback_days * 1.5) + 30)
    return start_dt.strftime("%Y%m%d"), end_date


# ============================================================
# Naver Finance crawling — Foreign / Institution flows
# ============================================================

def _flows_cache_key(ticker: str, start: str = None, end: str = None) -> str:
    today = datetime.now().strftime("%Y%m%d")
    return f"flows_{ticker}_{today}"


@disk_cached(_flows_cache_key, ttl=TTL_DAY)
def get_net_purchases(ticker: str, start: str = None, end: str = None) -> pd.DataFrame:
    """Recent foreign/institution net purchases from Naver. Cached 1 day.

    Scrapes https://finance.naver.com/item/frgn.naver
    Returns DataFrame with columns: date, close, change, volume, 기관합계, 외국인
    """
    rows = []
    for page in (1, 2):
        url = f"https://finance.naver.com/item/frgn.naver?code={ticker}&page={page}"
        try:
            r = requests.get(url, headers=NAVER_HEADERS, timeout=10)
            r.encoding = "euc-kr"
        except Exception as e:
            logger.debug(f"naver flows fetch fail {ticker} p{page}: {e}")
            return pd.DataFrame()
        time.sleep(0.05)  # polite rate limit

        soup = BeautifulSoup(r.text, "lxml")
        # The second `type2` table has daily foreign/institution rows
        tables = soup.find_all("table", class_="type2")
        if len(tables) < 2:
            continue
        table = tables[1]
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 7:
                continue
            date_txt = tds[0].get_text(strip=True)
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_txt):
                continue

            def _num(s):
                s = s.replace(",", "").replace("%", "").replace("+", "").strip()
                if s in ("", "-"):
                    return 0
                try:
                    return int(float(s))
                except ValueError:
                    return 0

            try:
                # Columns on naver frgn.naver (daily flows table):
                # 날짜, 종가, 전일비, 등락률, 거래량, 기관순매매량, 외국인순매매량,
                # 외국인 보유주수, 외국인 보유율
                rows.append({
                    "date": date_txt.replace(".", "-"),
                    "close": _num(tds[1].get_text()),
                    "기관합계": _num(tds[5].get_text()),
                    "외국인": _num(tds[6].get_text()),
                })
            except Exception:
                continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    return df


# ============================================================
# Naver Finance crawling — Fundamentals (PER/PBR/EPS/BPS)
# ============================================================

def _fund_cache_key(ticker: str, as_of: Optional[str] = None) -> str:
    week = datetime.now().strftime("%Y%W")
    return f"fund_{ticker}_{week}"


@disk_cached(_fund_cache_key, ttl=TTL_WEEK)
def get_fundamental(ticker: str, as_of: Optional[str] = None) -> dict:
    """Fetch PER / PBR / EPS / BPS / ROE from Naver main page. Cached 1 week.

    URL: https://finance.naver.com/item/main.naver?code=XXXXXX
    """
    url = f"https://finance.naver.com/item/main.naver?code={ticker}"
    try:
        r = requests.get(url, headers=NAVER_HEADERS, timeout=10)
        r.encoding = "euc-kr"
        time.sleep(0.05)
    except Exception as e:
        logger.debug(f"naver main fetch fail {ticker}: {e}")
        return {"per": None, "pbr": None, "eps": None, "bps": None}

    soup = BeautifulSoup(r.text, "lxml")
    # Find div#_per area
    out = {"per": None, "pbr": None, "eps": None, "bps": None, "roe": None}
    try:
        per_el = soup.select_one("#_per")
        if per_el:
            out["per"] = float(per_el.get_text(strip=True).replace(",", ""))
        eps_el = soup.select_one("#_eps")
        if eps_el:
            out["eps"] = float(eps_el.get_text(strip=True).replace(",", ""))
        pbr_el = soup.select_one("#_pbr")
        if pbr_el:
            out["pbr"] = float(pbr_el.get_text(strip=True).replace(",", ""))
        # BPS found in table
        for em in soup.select("em"):
            pass
    except Exception as e:
        logger.debug(f"parse fail {ticker}: {e}")

    return out


def get_fundamental_bulk(as_of: str, tickers: list[str] = None) -> pd.DataFrame:
    """Fetch fundamentals for many tickers. Returns df indexed by ticker
    with columns PER, PBR, EPS, BPS (uppercase for compatibility).

    If tickers is None, returns empty (caller must provide).
    """
    if not tickers:
        return pd.DataFrame()

    data = []
    for t in tickers:
        f = get_fundamental(t)
        data.append({
            "ticker": t,
            "PER": f.get("per") or 0,
            "PBR": f.get("pbr") or 0,
            "EPS": f.get("eps") or 0,
            "BPS": 0,  # not scraped; would need additional parsing
            "DIV": 0,
            "DPS": 0,
        })
    df = pd.DataFrame(data).set_index("ticker")
    return df
