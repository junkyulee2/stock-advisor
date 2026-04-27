"""Sell signal evaluation for the web UI.

Two streams of signals:

1) HARD rules (price action only, no external data fetch)
   - Hard stop loss (-15%) → forced 100%
   - Time stop (20 days) → forced 100%
   - Partial take profit (+20%) → sell 50%
   - Trailing stop (-8% from peak) → forced 100%
   - Tight trailing (-5% after partial taken) → forced 100% (lock locked-in profit)

2) FACTOR DEGRADATION (compare entry_factors snapshot to today's score row)
   - Total score plunge (-15p) → counts as 1 sign
   - Floor breach (entry ≥ 80 → today < 80) → counts as 1 sign
   - Each of 5 per-factor drops ≥ 20p → 1 sign each
   Aggregated:
     0 signs  → safe
     1 sign   → caution
     2 signs  → warn (50% sell suggestion)
     3+ signs → sell ("매수 근거 붕괴", 100% sell suggestion)

Hard rules trump degradation (immediate price action > slow factor erosion).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


# Factor metadata: (entry_factors key, scores_record key, Korean label)
FACTOR_META = [
    ("momentum",       "momentum_score",       "모멘텀"),
    ("supply_demand",  "supply_demand_score",  "수급"),
    ("quality",        "quality_score",        "퀄리티"),
    ("volatility",     "volatility_score",     "변동성"),
    ("mean_reversion", "mean_reversion_score", "역추세"),
]


LEVEL_RANK = {"safe": 0, "caution": 1, "warn": 2, "sell": 3, "forced": 4}


def _days_held(entry_date: str, today: Optional[datetime] = None) -> int:
    today = today or datetime.now()
    try:
        d = datetime.strptime(entry_date, "%Y-%m-%d")
        return max(0, (today - d).days)
    except Exception:
        return 0


def evaluate_hard_rules(pos: dict, current_price: float, config: dict,
                        today: Optional[datetime] = None) -> Optional[dict]:
    """Return a signal dict if any HARD rule triggers, else None."""
    rules = config.get("sell_rules", {})
    entry = float(pos.get("entry_price", 0) or 0)
    if entry <= 0:
        return None
    ret = (current_price / entry - 1) * 100

    # 1. hard stop loss
    sl = rules.get("hard_stop_loss_pct", -15)
    if ret <= sl:
        return {"level": "forced", "label": "🚨 손절",
                "reason": f"손절선 발동 ({ret:+.1f}% ≤ {sl}%)", "ratio": 1.0}

    # 2. time stop
    ts = rules.get("time_stop_days", 20)
    held = _days_held(pos.get("entry_date", ""), today)
    if held >= ts:
        return {"level": "forced", "label": "⏰ 시간만료",
                "reason": f"{held}일 보유 (시간 손절 {ts}일)", "ratio": 1.0}

    # 3. partial take profit
    tp = rules.get("hard_take_profit_partial_pct", 20)
    tp_ratio = rules.get("hard_take_profit_partial_ratio", 0.5)
    partial_taken = bool(pos.get("partial_taken", False))
    if ret >= tp and not partial_taken:
        return {"level": "sell", "label": "💰 부분 익절",
                "reason": f"+{ret:.1f}% 도달 → {int(tp_ratio*100)}% 매도 권고",
                "ratio": tp_ratio}

    # 4. trailing stop (tight if partial taken, normal otherwise)
    highest = float(pos.get("highest_price", entry) or entry)
    if highest > entry:
        dd = (current_price / highest - 1) * 100
        tight_pct = -5.0
        normal_pct = rules.get("trailing_stop_pct", -8)
        if partial_taken and dd <= tight_pct:
            return {"level": "forced", "label": "📉 잔여 청산",
                    "reason": f"부분익절 후 고점 {dd:+.1f}% (tight ≤ {tight_pct}%)",
                    "ratio": 1.0}
        if dd <= normal_pct:
            return {"level": "forced", "label": "📉 트레일링 손절",
                    "reason": f"고점 ₩{highest:,.0f} 대비 {dd:+.1f}%",
                    "ratio": 1.0}

    return None


def evaluate_degradation(pos: dict, current_score_row: Optional[dict],
                         floor_score: int = 80) -> dict:
    """Compare entry_factors snapshot vs today's score row.

    Returns: {level, signs[], summary, comparable, delta_total}.
    Empty entry_factors (legacy positions, pre-2026-04-27 buys) → comparable=False.
    """
    entry_factors = pos.get("entry_factors") or {}
    entry_total = float(pos.get("entry_score", 0) or 0)

    if not entry_factors or current_score_row is None:
        return {"level": "safe", "signs": [], "summary": "비교 불가 (factor 데이터 부족)",
                "comparable": False, "delta_total": 0.0}

    cur_total = float(current_score_row.get("total_score", 0) or 0)
    delta_total = cur_total - entry_total
    signs: list[dict] = []

    # 1. Total score plunge
    if delta_total <= -15:
        signs.append({"key": "total", "label": "종합",
                      "entry": entry_total, "current": cur_total, "delta": delta_total})

    # 2. Floor breach (was buyable, no longer)
    if entry_total >= floor_score and cur_total < floor_score \
            and not any(s["key"] == "total" for s in signs):
        signs.append({"key": "floor", "label": f"마지노선({floor_score})",
                      "entry": entry_total, "current": cur_total,
                      "delta": cur_total - floor_score})

    # 3. Per-factor deterioration (≥ 20p drop)
    for ekey, ckey, label in FACTOR_META:
        e_val = float(entry_factors.get(ekey, 0) or 0)
        c_val = float(current_score_row.get(ckey, 0) or 0)
        delta = c_val - e_val
        if delta <= -20:
            signs.append({"key": ekey, "label": label,
                          "entry": e_val, "current": c_val, "delta": delta})

    n = len(signs)
    if n == 0:
        level, summary = "safe", "팩터 양호"
    elif n == 1:
        level, summary = "caution", "약화 신호 1개"
    elif n == 2:
        level, summary = "warn", "약화 신호 2개"
    else:
        level, summary = "sell", f"매수 근거 붕괴 ({n})"

    return {"level": level, "signs": signs, "summary": summary,
            "comparable": True, "delta_total": delta_total}


def combine_signals(hard: Optional[dict], degradation: dict) -> dict:
    """Pick dominant signal. Hard rules trump degradation."""
    if hard is not None:
        return {
            "level": hard["level"],
            "label": hard["label"],
            "reason": hard["reason"],
            "ratio": hard.get("ratio", 1.0),
            "source": "hard",
            "degradation": degradation,
        }
    return {
        "level": degradation["level"],
        "label": _degradation_label(degradation),
        "reason": degradation["summary"],
        "ratio": {"sell": 1.0, "warn": 0.5}.get(degradation["level"], 0.0),
        "source": "degradation",
        "degradation": degradation,
    }


def _degradation_label(d: dict) -> str:
    if not d.get("comparable"):
        return "· 비교 불가"
    n = len(d.get("signs", []))
    return ["✓ 안전", "⚠ 주의", "⚠ 약화", "🚨 매수 근거 붕괴"][min(n, 3)]


def signal_severity(level: str) -> int:
    return LEVEL_RANK.get(level, 0)
