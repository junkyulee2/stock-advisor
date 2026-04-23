"""Sell signal engine.

Two mechanisms:
1. HARD rules — always trigger (stop-loss, take-profit, time stop).
2. SELL SCORE — continuous measure of bearish strength; staged exits.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from . import indicators as ind


def compute_return_pct(entry_price: float, current_price: float) -> float:
    return (current_price / entry_price - 1) * 100 if entry_price > 0 else 0.0


def days_held(entry_date: str, today: Optional[str] = None) -> int:
    """Calendar days held (as proxy; trading days would be more accurate)."""
    d_entry = datetime.strptime(entry_date, "%Y-%m-%d")
    d_today = datetime.strptime(today, "%Y-%m-%d") if today else datetime.now()
    return (d_today - d_entry).days


def check_hard_rules(
    position: dict,
    current_price: float,
    config: dict,
    today: Optional[str] = None,
) -> Optional[dict]:
    """Return an exit order dict if a hard rule triggers, else None.

    position schema: {entry_price, entry_date, qty, highest_price, partial_taken}
    Exit order: {reason, sell_ratio, priority}
    """
    rules = config["sell_rules"]
    entry = position["entry_price"]
    ret = compute_return_pct(entry, current_price)

    # 1. Hard stop loss
    if ret <= rules["hard_stop_loss_pct"]:
        return {"reason": f"hard_stop_loss ({ret:.2f}%)", "sell_ratio": 1.0, "priority": 1}

    # 2. Time stop
    held = days_held(position["entry_date"], today)
    if held >= rules["time_stop_days"]:
        return {"reason": f"time_stop ({held}d)", "sell_ratio": 1.0, "priority": 2}

    # 3. Partial take-profit at +X%
    if (
        ret >= rules["hard_take_profit_partial_pct"]
        and not position.get("partial_taken", False)
    ):
        return {
            "reason": f"take_profit_partial (+{ret:.2f}%)",
            "sell_ratio": rules["hard_take_profit_partial_ratio"],
            "priority": 3,
        }

    # 4. Trailing stop from highest since entry
    highest = position.get("highest_price", entry)
    if highest > 0:
        drop_from_peak = (current_price / highest - 1) * 100
        if drop_from_peak <= rules["trailing_stop_pct"]:
            return {
                "reason": f"trailing_stop (peak {highest:.0f} -> {current_price:.0f})",
                "sell_ratio": 1.0,
                "priority": 4,
            }

    return None


def compute_sell_score(
    price_df: pd.DataFrame,
    flows_df: pd.DataFrame,
    position: dict,
    config: dict,
) -> tuple[float, list[str]]:
    """Compute sell score (0-100). Higher = more bearish.

    Components:
      - momentum_reversal: 20d ret negative + price below 20MA
      - supply_deterioration: foreign net sell for 2-3 days
      - technical_breakdown: MA5 break with volume expansion
      - news_negative: placeholder (news module TBD)
    """
    weights = config["sell_scoring"]
    reasons = []
    score = 0.0

    if price_df.empty or len(price_df) < 20:
        return 0.0, ["insufficient_data"]

    close = price_df["close"]
    volume = price_df["volume"]
    last_close = close.iloc[-1]

    # --- Momentum reversal ---
    ma20 = ind.sma(close, 20).iloc[-1]
    ret_20 = (close.iloc[-1] / close.iloc[-20] - 1) if len(close) >= 21 else 0
    mom_rev = 0
    if pd.notna(ma20) and last_close < ma20:
        mom_rev += 0.5
    if ret_20 < 0:
        mom_rev += 0.5
    score += mom_rev * weights["momentum_reversal"]
    if mom_rev > 0:
        reasons.append(f"momentum_reversal({mom_rev:.1f})")

    # --- Supply deterioration ---
    sd = 0.0
    neg_days = 0
    foreign_col = None
    if not flows_df.empty:
        for c in ("외국인합계", "외국인"):
            if c in flows_df.columns:
                foreign_col = c
                break
    if foreign_col:
        last3 = flows_df.tail(3)[foreign_col]
        neg_days = sum(1 for v in last3 if v < 0)
        if neg_days >= 2:
            sd = 0.7 if neg_days == 2 else 1.0
    score += sd * weights["supply_deterioration"]
    if sd > 0:
        reasons.append(f"foreign_sell({neg_days}d)")

    # --- Technical breakdown ---
    tb = 0.0
    ma5 = ind.sma(close, 5).iloc[-1]
    prev_close = close.iloc[-2] if len(close) >= 2 else last_close
    if pd.notna(ma5) and prev_close >= ma5 and last_close < ma5:
        # Freshly broke MA5
        tb = 0.6
        # Check for volume expansion
        vol20 = volume.rolling(20).mean().iloc[-1]
        if pd.notna(vol20) and volume.iloc[-1] > vol20 * 1.5:
            tb = 1.0
    score += tb * weights["technical_breakdown"]
    if tb > 0:
        reasons.append(f"ma5_break({tb:.1f})")

    # news_negative placeholder — 0 for now

    return min(score, 100.0), reasons


def decide_exit(
    position: dict,
    price_df: pd.DataFrame,
    flows_df: pd.DataFrame,
    current_price: float,
    config: dict,
    today: Optional[str] = None,
) -> Optional[dict]:
    """Decide exit action. Priority: hard rules > sell score.

    Returns {action, sell_ratio, reason, sell_score} or None.
    """
    hard = check_hard_rules(position, current_price, config, today)
    if hard:
        return {
            "action": "sell",
            "sell_ratio": hard["sell_ratio"],
            "reason": hard["reason"],
            "sell_score": None,
            "source": "hard_rule",
        }

    sell_score, reasons = compute_sell_score(price_df, flows_df, position, config)
    stage1 = config["sell_rules"]["sell_score_stage1"]
    stage2 = config["sell_rules"]["sell_score_stage2"]

    partial_taken = position.get("partial_taken", False)
    if sell_score >= stage2:
        return {
            "action": "sell",
            "sell_ratio": 1.0,
            "reason": f"sell_score_stage2 ({sell_score:.1f}): {','.join(reasons)}",
            "sell_score": sell_score,
            "source": "score",
        }
    if sell_score >= stage1 and not partial_taken:
        return {
            "action": "sell",
            "sell_ratio": 0.5,
            "reason": f"sell_score_stage1 ({sell_score:.1f}): {','.join(reasons)}",
            "sell_score": sell_score,
            "source": "score",
        }
    return None
