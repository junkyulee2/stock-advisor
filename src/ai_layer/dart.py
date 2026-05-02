"""DART (전자공시) disclosure fetcher.

Pulls last-30-day disclosure list per ticker from OpenDART's free API.
Used as the *qualitative* input dimension for the AI verdict layer —
the rule engine sees prices and volumes; this layer sees what the
company has filed.

Free API key: https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do
After sign-up, set env var DART_API_KEY (or override config.yaml).

Without a key, every fetch returns []. AI verdict still runs but with
factor data only — degraded but not broken.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import requests

from src.utils import setup_logger

logger = setup_logger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"
CORP_CODE_TTL_DAYS = 7
CORP_CACHE = Path("data/cache/dart_corp_codes.json")


# ---------- API key ----------

def _api_key(config: dict) -> Optional[str]:
    env_var = config.get("ai_layer", {}).get("dart_api_key_env", "DART_API_KEY")
    return os.environ.get(env_var)


# ---------- corp_code mapping (ticker -> 8-digit DART corp_code) ----------

def _fetch_corp_codes(api_key: str) -> dict[str, str]:
    """Download CORPCODE.xml ZIP, parse ticker -> corp_code map.
    Returns dict keyed by 6-digit stock_code (zero-padded)."""
    url = f"{DART_BASE}/corpCode.xml"
    r = requests.get(url, params={"crtfc_key": api_key}, timeout=30)
    r.raise_for_status()

    mapping: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        with zf.open("CORPCODE.xml") as f:
            tree = ET.parse(f)
    for el in tree.getroot().findall("list"):
        stock_code = (el.findtext("stock_code") or "").strip()
        corp_code = (el.findtext("corp_code") or "").strip()
        if stock_code and corp_code and stock_code.isdigit():
            mapping[stock_code.zfill(6)] = corp_code
    return mapping


def load_corp_codes(config: dict, *, force_refresh: bool = False) -> dict[str, str]:
    """Get ticker -> corp_code map. Cached for 7 days."""
    api_key = _api_key(config)
    if not api_key:
        return {}

    CORP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if not force_refresh and CORP_CACHE.exists():
        age_days = (time.time() - CORP_CACHE.stat().st_mtime) / 86400
        if age_days < CORP_CODE_TTL_DAYS:
            try:
                return json.loads(CORP_CACHE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"corp_code cache unreadable: {e}; refreshing")

    try:
        mapping = _fetch_corp_codes(api_key)
        CORP_CACHE.write_text(json.dumps(mapping, ensure_ascii=False),
                              encoding="utf-8")
        logger.info(f"DART corp_code refreshed: {len(mapping)} tickers")
        return mapping
    except Exception as e:
        logger.warning(f"DART corp_code fetch failed: {e}")
        if CORP_CACHE.exists():
            try:
                return json.loads(CORP_CACHE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}


# ---------- disclosure fetching ----------

# Risk-bearing filing types worth flagging in summaries. Full type code list:
# https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001
RISK_KEYWORDS = (
    "유상증자", "무상증자", "전환사채", "신주인수권부사채", "교환사채",
    "자기주식", "감자", "합병", "분할",
    "영업정지", "회생절차", "감사의견", "횡령", "배임",
    "최대주주변경", "관리종목", "거래정지",
)


def _classify(title: str) -> Optional[str]:
    """Return short risk label if title matches any RISK_KEYWORDS."""
    for kw in RISK_KEYWORDS:
        if kw in title:
            return kw
    return None


def fetch_disclosures(
    config: dict,
    ticker: str,
    *,
    days: int = 30,
    corp_code_map: Optional[dict[str, str]] = None,
) -> list[dict]:
    """Fetch DART disclosures for one ticker over the last `days`.
    Returns [] on no key, no map entry, or API error.

    Each row: {date, title, report_no, type_label or None, dart_url}
    """
    api_key = _api_key(config)
    if not api_key:
        return []

    if corp_code_map is None:
        corp_code_map = load_corp_codes(config)
    corp = corp_code_map.get(ticker.zfill(6))
    if not corp:
        return []

    end = datetime.now()
    bgn = end - timedelta(days=days)
    params = {
        "crtfc_key": api_key,
        "corp_code": corp,
        "bgn_de": bgn.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "page_count": 100,
    }
    try:
        r = requests.get(f"{DART_BASE}/list.json", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"DART list fetch failed for {ticker}: {e}")
        return []

    if data.get("status") not in ("000", "013"):  # 013 = no results
        logger.warning(f"DART API status for {ticker}: {data.get('status')} {data.get('message')}")
        return []

    out = []
    for row in data.get("list", []) or []:
        title = (row.get("report_nm") or "").strip()
        rcept_no = row.get("rcept_no") or ""
        out.append({
            "date": row.get("rcept_dt", ""),
            "title": title,
            "report_no": rcept_no,
            "type_label": _classify(title),
            "dart_url": (
                f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                if rcept_no else None
            ),
        })
    return out


def disclosures_for_candidates(
    config: dict,
    tickers: list[str],
    *,
    days: Optional[int] = None,
) -> dict[str, list[dict]]:
    """Bulk fetch — returns {ticker: [disclosures...]}."""
    days = days or int(config.get("ai_layer", {}).get("dart_lookback_days", 30))
    corp_map = load_corp_codes(config)
    if not corp_map:
        return {t: [] for t in tickers}

    out: dict[str, list[dict]] = {}
    for t in tickers:
        out[t] = fetch_disclosures(config, t, days=days, corp_code_map=corp_map)
        # OpenDART ratelimit: ~10 req/s, be safe
        time.sleep(0.12)
    return out


def summarize_for_prompt(disclosures: list[dict], max_items: int = 12) -> list[dict]:
    """Compact form for sending to Claude. Drops URLs, keeps date/title/risk."""
    risky = [d for d in disclosures if d.get("type_label")]
    others = [d for d in disclosures if not d.get("type_label")]
    picked = (risky + others)[:max_items]
    return [
        {"date": d.get("date"), "title": d.get("title"), "risk": d.get("type_label")}
        for d in picked
    ]
