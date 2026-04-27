"""Business operations: buy/sell/add/refresh. Returns plain dicts (no HTTP)."""
from __future__ import annotations

from typing import Optional

from src import portfolio as pf, cloud_store

from .data_layer import (
    CONFIG, CLOUD_MODE,
    PORTFOLIO_PATH, HISTORY_PATH,
    cloud_read, cloud_write,
    load_portfolio, load_history,
)


# ---------- internal helpers ----------

def _latest_pair():
    portfolio, p_sha = load_portfolio()
    history, h_sha = load_history()
    return portfolio, p_sha, history, h_sha


def _commit_pair(portfolio, p_sha, history, h_sha, msg_prefix: str):
    ok1 = cloud_write(PORTFOLIO_PATH, portfolio, p_sha, f"web: {msg_prefix} (portfolio)")
    ok2 = cloud_write(HISTORY_PATH, history, h_sha, f"web: {msg_prefix} (history)")
    return ok1 and ok2


# ---------- operations ----------

def _extract_factors(rec: dict) -> dict:
    """Pull 5-factor breakdown out of a scores record. Used to snapshot
    factor strength at entry for later 'factor degradation' sell signals."""
    return {
        "momentum":       float(rec.get("momentum_score", 0) or 0),
        "supply_demand":  float(rec.get("supply_demand_score", 0) or 0),
        "quality":        float(rec.get("quality_score", 0) or 0),
        "volatility":     float(rec.get("volatility_score", 0) or 0),
        "mean_reversion": float(rec.get("mean_reversion_score", 0) or 0),
    }


def buy(rec: dict, amount_krw: int) -> tuple[bool, str]:
    price = float(rec.get("close", 0) or 0)
    if price <= 0:
        return False, "가격이 0입니다"

    portfolio, p_sha, history, h_sha = _latest_pair()
    ticker = rec["ticker"]
    if ticker in portfolio["positions"]:
        return False, "이미 보유 중입니다"

    try:
        pos = pf.buy(
            portfolio, ticker=ticker, name=rec["name"],
            price=price, amount_krw=amount_krw,
            score=float(rec["total_score"]),
            factors=_extract_factors(rec),
        )
        pos["mode"] = "simulation"
        pf.record_buy_history(history, pos)
    except Exception as e:
        return False, f"매수 계산 실패: {e}"

    if not _commit_pair(portfolio, p_sha, history, h_sha,
                        f"buy {ticker} @ {price:,.0f}"):
        return False, "저장 실패"
    return True, f"매수 완료: {pos['name']} {pos['qty']}주 @ ₩{price:,.0f}"


def add(ticker: str, price: float, amount_krw: int, score: float) -> tuple[bool, str]:
    if price <= 0:
        return False, "가격이 0입니다"

    portfolio, p_sha, history, h_sha = _latest_pair()
    if ticker not in portfolio["positions"]:
        return False, "보유 종목이 아닙니다"

    limits = CONFIG.get("portfolio_limits", {})
    max_adds = int(limits.get("max_adds_per_position", 3))
    existing_adds = portfolio["positions"][ticker].get("add_count", 0)
    if existing_adds >= max_adds:
        return False, f"추가 매수 한도 초과 (최대 {max_adds}회)"

    try:
        pos, new_qty = pf.add_to_position(
            portfolio, ticker=ticker, price=price,
            amount_krw=amount_krw, score=score,
        )
        pf.record_add_history(history, ticker=ticker, name=pos["name"],
                              qty=new_qty, price=price, score=score)
    except Exception as e:
        return False, f"추가 매수 계산 실패: {e}"

    if not _commit_pair(portfolio, p_sha, history, h_sha,
                        f"add {ticker} +{new_qty}주 @ {price:,.0f}"):
        return False, "저장 실패"
    return True, f"추가 매수: {pos['name']} +{new_qty}주 @ ₩{price:,.0f}"


def sell(ticker: str, current_price: float, sell_ratio: float,
         reason: str = "manual") -> tuple[bool, str]:
    portfolio, p_sha, history, h_sha = _latest_pair()
    if ticker not in portfolio["positions"]:
        return False, "보유 종목이 아닙니다"

    try:
        trade = pf.sell(
            portfolio, history, ticker=ticker, price=current_price,
            sell_ratio=sell_ratio, reason=reason,
        )
    except Exception as e:
        return False, f"매도 계산 실패: {e}"

    if not _commit_pair(portfolio, p_sha, history, h_sha,
                        f"sell {ticker} {int(sell_ratio*100)}%"):
        return False, "저장 실패"
    pct_str = f"{trade['pnl_pct']:+.2f}%"
    return True, (f"매도 완료: {trade['name']} "
                  f"{trade['pnl_krw']:+,.0f}원 ({pct_str})")


def trigger_refresh() -> tuple[bool, str]:
    """Dispatch GitHub Actions to recompute scores. Cloud mode only."""
    if not CLOUD_MODE:
        return False, "클라우드 모드가 아닙니다 (로컬에선 run_daily.py 직접 실행)"
    try:
        cloud_store.trigger_workflow()
    except Exception as e:
        return False, f"갱신 요청 실패: {e}"
    return True, "✅ 점수 재계산 요청됨. 5~10분 후 새로고침."


def workflow_status() -> Optional[dict]:
    if not CLOUD_MODE:
        return None
    try:
        return cloud_store.last_workflow_run()
    except Exception:
        return None
