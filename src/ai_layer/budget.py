"""Token budget tracker for the AI layer.

Persists monthly usage to `data/ai_usage.json`. Enforces a hard monthly cap
and emits Discord alerts at configured thresholds (default 50%, 80%, 100%).

The user runs Claude Code for unrelated work too, so this budget is
SEPARATE from their general subscription headroom — it limits only what
this stock-advisor process consumes.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src import notifier
from src.utils import setup_logger

logger = setup_logger(__name__)


class BudgetExceeded(RuntimeError):
    """Raised when a planned AI call would exceed the monthly token budget."""


# ---------- persistence ----------

def _path(config: dict) -> Path:
    p = config.get("paths", {}).get("ai_usage", "data/ai_usage.json")
    return Path(p)


def _empty_record(month: str, budget: int) -> dict[str, Any]:
    return {
        "current_month": month,
        "monthly_budget_tokens": budget,
        "tokens_used_this_month": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "calls": 0,
        "warnings_sent": [],          # e.g. [50, 80, 100]
        "fallback_active": False,     # True after monthly cap hit
        "daily_log": [],              # last 60 entries
    }


def load_usage(config: dict) -> dict[str, Any]:
    """Load current usage record. Auto-rolls over on month change."""
    cfg = config.get("ai_layer", {})
    budget = int(cfg.get("monthly_budget_tokens", 5_000_000))
    month = datetime.now().strftime("%Y-%m")

    p = _path(config)
    if not p.exists():
        return _empty_record(month, budget)

    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"ai_usage.json unreadable, resetting: {e}")
        return _empty_record(month, budget)

    # Month rollover
    if rec.get("current_month") != month:
        # Archive previous month's totals into daily_log header (informational)
        prev = {
            "month_closed": rec.get("current_month"),
            "tokens_total": rec.get("tokens_used_this_month", 0),
            "calls": rec.get("calls", 0),
        }
        new = _empty_record(month, budget)
        new["daily_log"] = ([{"event": "month_rollover", **prev}] + rec.get("daily_log", []))[:60]
        return new

    # Budget may have been edited in config; pick up the new value
    rec["monthly_budget_tokens"] = budget
    return rec


def save_usage(config: dict, rec: dict) -> None:
    p = _path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- budget enforcement ----------

def check_budget(config: dict, planned_input_tokens: int = 0) -> dict:
    """Return current usage record. Raises BudgetExceeded if monthly cap is
    already hit AND fallback_on_cap is True (caller should skip AI gracefully).

    `planned_input_tokens` is advisory — used only for pre-flight estimation
    when caller wants a heads-up; we don't reject mid-window.
    """
    cfg = config.get("ai_layer", {})
    rec = load_usage(config)

    used = int(rec.get("tokens_used_this_month", 0))
    budget = int(rec.get("monthly_budget_tokens", 5_000_000))
    fallback_on_cap = bool(cfg.get("fallback_on_cap", True))

    if used >= budget and fallback_on_cap:
        rec["fallback_active"] = True
        save_usage(config, rec)
        raise BudgetExceeded(
            f"Monthly AI budget exhausted: {used:,} / {budget:,} tokens. "
            f"Fallback to score-only mode."
        )
    return rec


# ---------- usage recording ----------

def record_usage(
    config: dict,
    input_tokens: int,
    output_tokens: int,
    *,
    candidates_n: int = 0,
    note: str = "",
) -> dict:
    """Record one AI call's token usage. Sends Discord alerts when crossing
    configured thresholds. Returns updated record.
    """
    cfg = config.get("ai_layer", {})
    warn_pcts = sorted(set(int(p) for p in cfg.get("warn_pct", [50, 80])))
    rec = load_usage(config)

    total_tokens = int(input_tokens) + int(output_tokens)
    rec["tokens_used_this_month"] = int(rec.get("tokens_used_this_month", 0)) + total_tokens
    rec["input_tokens"] = int(rec.get("input_tokens", 0)) + int(input_tokens)
    rec["output_tokens"] = int(rec.get("output_tokens", 0)) + int(output_tokens)
    rec["calls"] = int(rec.get("calls", 0)) + 1

    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    rec.setdefault("daily_log", []).insert(0, {
        "ts": today,
        "input": int(input_tokens),
        "output": int(output_tokens),
        "candidates": int(candidates_n),
        "note": note,
    })
    rec["daily_log"] = rec["daily_log"][:60]

    # Threshold alerts
    budget = int(rec.get("monthly_budget_tokens", 5_000_000))
    pct = (rec["tokens_used_this_month"] / budget) * 100 if budget > 0 else 0
    sent: list[int] = list(rec.get("warnings_sent", []))
    for threshold in warn_pcts + [100]:
        if pct >= threshold and threshold not in sent:
            _send_threshold_alert(threshold, rec)
            sent.append(threshold)
    rec["warnings_sent"] = sent

    if pct >= 100:
        rec["fallback_active"] = True

    save_usage(config, rec)
    return rec


def _send_threshold_alert(threshold: int, rec: dict) -> None:
    used = int(rec.get("tokens_used_this_month", 0))
    budget = int(rec.get("monthly_budget_tokens", 1))
    month = rec.get("current_month", "?")

    if threshold == 100:
        notifier.send_message(
            f"🛑 **AI 예산 100% 소진** ({month})\n"
            f"- 사용: {used:,} / {budget:,} tokens\n"
            f"- AI 레이어 자동 OFF — 점수만으로 추천 동작 중\n"
            f"- 다음 달까지 기다리거나 config.yaml `monthly_budget_tokens` 상향, "
            f"또는 설계 재검토 필요"
        )
    elif threshold >= 80:
        notifier.send_message(
            f"⚠️ **AI 예산 {threshold}% 도달** ({month})\n"
            f"- 사용: {used:,} / {budget:,} tokens\n"
            f"- 100% 도달 시 AI 자동 OFF. 사용 패턴 점검 권장."
        )
    else:
        notifier.send_message(
            f"ℹ️ AI 예산 {threshold}% 사용 ({month}) — {used:,} / {budget:,} tokens"
        )


def usage_summary(config: dict) -> dict:
    """Compact summary for UI / debug endpoints."""
    rec = load_usage(config)
    used = int(rec.get("tokens_used_this_month", 0))
    budget = int(rec.get("monthly_budget_tokens", 1))
    return {
        "month": rec.get("current_month"),
        "used": used,
        "budget": budget,
        "pct": round((used / budget) * 100, 1) if budget > 0 else 0,
        "calls": int(rec.get("calls", 0)),
        "fallback_active": bool(rec.get("fallback_active", False)),
        "input_tokens": int(rec.get("input_tokens", 0)),
        "output_tokens": int(rec.get("output_tokens", 0)),
    }
