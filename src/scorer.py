"""4-factor scoring engine.

Factors: Momentum, Supply/Demand, Quality, Mean Reversion.
Each factor is scored against ABSOLUTE thresholds defined in config.yaml
(linear interpolation). A small "top of day" bonus is added to the final
composite so ordinary markets still surface 2-3 picks/week while weak
markets correctly produce no recommendations.

Lookahead-bias prevention: the `as_of` date is the cutoff. No future data used.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .utils import setup_logger

logger = setup_logger(__name__)


def _clip_percentile(s: pd.Series) -> pd.Series:
    return s.rank(pct=True) * 100


def threshold_score(value: float, thresholds: list[dict]) -> float:
    """Linear interpolation between (value, score) points.

    Values outside the defined range clamp to the nearest score.
    Used for absolute factor scoring per config.yaml:scoring.absolute_thresholds.
    """
    if value is None or not thresholds:
        return 50.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 50.0
    # Sort by value ascending
    ts = sorted(thresholds, key=lambda t: float(t["value"]))
    values = [float(t["value"]) for t in ts]
    scores = [float(t["score"]) for t in ts]

    if v <= values[0]:
        return scores[0]
    if v >= values[-1]:
        return scores[-1]
    for i in range(len(values) - 1):
        if values[i] <= v <= values[i + 1]:
            span = values[i + 1] - values[i]
            if span == 0:
                return scores[i]
            t = (v - values[i]) / span
            return scores[i] + t * (scores[i + 1] - scores[i])
    return 50.0


def compute_momentum_scores(
    price_panel: dict[str, pd.DataFrame],
    benchmark: pd.Series,
    config: dict,
) -> pd.DataFrame:
    """For each ticker, compute relative returns over 5/20/60 days.

    Returns DataFrame indexed by ticker with columns:
      ret_5, ret_20, ret_60, momentum_score (0-100)
    """
    cfg = config["scoring"]["momentum"]
    rows = []
    for ticker, df in price_panel.items():
        if df.empty or len(df) < 65:
            continue
        close = df["close"]
        r5 = ind.relative_return(close, benchmark, 5)
        r20 = ind.relative_return(close, benchmark, 20)
        r60 = ind.relative_return(close, benchmark, 60)
        rows.append({"ticker": ticker, "ret_5": r5, "ret_20": r20, "ret_60": r60})

    out = pd.DataFrame(rows).set_index("ticker")
    if out.empty:
        return out

    # Percentile-rank each horizon, then weighted combine
    p5 = _clip_percentile(out["ret_5"])
    p20 = _clip_percentile(out["ret_20"])
    p60 = _clip_percentile(out["ret_60"])

    w5 = cfg["return_5d_weight"]
    w20 = cfg["return_20d_weight"]
    w60 = cfg["return_60d_weight"]
    wsum = w5 + w20 + w60

    out["momentum_score"] = (p5 * w5 + p20 * w20 + p60 * w60) / wsum
    return out


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def compute_supply_demand_scores(
    flows_panel: dict[str, pd.DataFrame],
    market_cap: pd.Series,
    config: dict,
) -> pd.DataFrame:
    """Foreign/institution 5-day cumulative net buy, normalized by market cap."""
    cfg = config["scoring"]["supply_demand"]
    rows = []
    foreign_candidates = ["외국인합계", "외국인"]
    inst_candidates = ["기관합계"]

    for ticker, df in flows_panel.items():
        if df.empty or len(df) < 5:
            continue
        f_col = _pick_col(df, foreign_candidates)
        i_col = _pick_col(df, inst_candidates)
        if not f_col and not i_col:
            continue

        last5 = df.tail(5)
        foreign_5d = last5[f_col].sum() if f_col else 0
        inst_5d = last5[i_col].sum() if i_col else 0

        # Consecutive foreign buy days
        consec = 0
        if f_col:
            for v in reversed(df[f_col].tail(5).tolist()):
                if v > 0:
                    consec += 1
                else:
                    break

        cap = market_cap.get(ticker, np.nan)
        f_ratio = foreign_5d / cap if cap and cap > 0 else 0
        i_ratio = inst_5d / cap if cap and cap > 0 else 0

        rows.append({
            "ticker": ticker,
            "foreign_5d": foreign_5d,
            "inst_5d": inst_5d,
            "foreign_ratio": f_ratio,
            "inst_ratio": i_ratio,
            "foreign_consec": consec,
        })

    out = pd.DataFrame(rows).set_index("ticker")
    if out.empty:
        return out

    pf = _clip_percentile(out["foreign_ratio"])
    pi = _clip_percentile(out["inst_ratio"])
    pc = (out["foreign_consec"] / 5) * 100  # 0..100

    wf = cfg["foreign_5d_weight"]
    wi = cfg["institution_5d_weight"]
    wc = cfg["foreign_consecutive"]
    ws = cfg.get("short_ratio_weight", 0)
    wsum = wf + wi + wc + ws

    # Short ratio placeholder: assume 50 percentile if not provided
    ps = pd.Series(50, index=out.index)

    out["supply_demand_score"] = (pf * wf + pi * wi + pc * wc + ps * ws) / wsum
    return out


def compute_quality_scores(
    fundamentals: pd.DataFrame,
    close_by_ticker: pd.Series,
    config: dict,
) -> pd.DataFrame:
    """Quality: earnings yield (EPS/Price), low debt proxy via PBR inverse, etc.

    `fundamentals` is pykrx fundamental df indexed by ticker with PER/PBR/EPS/BPS/DIV/DPS.
    We cannot get ROE directly from pykrx free. Proxy: 1/PBR or EPS/BPS.
    """
    cfg = config["scoring"]["quality"]
    if fundamentals.empty:
        return pd.DataFrame()

    df = fundamentals.copy()
    # Earnings Yield = EPS / Price = 1 / PER (when PER > 0)
    df["earnings_yield"] = df.apply(
        lambda r: (1 / r["PER"]) if r.get("PER", 0) > 0 else 0, axis=1
    )
    # ROE proxy: EPS / BPS
    df["roe_proxy"] = df.apply(
        lambda r: (r["EPS"] / r["BPS"]) if r.get("BPS", 0) > 0 and r.get("EPS", 0) > 0 else 0,
        axis=1,
    )
    # PBR low = cheap book (debt/financial health proxy, not perfect)
    df["pbr_inv"] = df.apply(
        lambda r: (1 / r["PBR"]) if r.get("PBR", 0) > 0 else 0, axis=1
    )

    p_ey = _clip_percentile(df["earnings_yield"])
    p_roe = _clip_percentile(df["roe_proxy"])
    p_pbr = _clip_percentile(df["pbr_inv"])

    w_roe = cfg["roe_weight"]
    w_ey = cfg["earnings_yield_weight"]
    w_debt = cfg["debt_ratio_weight"]   # using pbr_inv as proxy
    w_op = cfg.get("op_margin_weight", 0)  # no free op_margin -> fold into earnings_yield
    wsum = w_roe + w_ey + w_debt + w_op

    # op_margin placeholder = 50 if no data
    p_op = pd.Series(50, index=df.index)

    df["quality_score"] = (p_roe * w_roe + p_ey * w_ey + p_pbr * w_debt + p_op * w_op) / wsum
    return df[["earnings_yield", "roe_proxy", "pbr_inv", "quality_score"]]


def compute_mean_reversion_scores(
    price_panel: dict[str, pd.DataFrame],
    config: dict,
) -> pd.DataFrame:
    """Mean reversion: RSI oversold with upturn + Bollinger lower band bounce.

    Only gives score when signal is actually present; otherwise close to 0.
    """
    cfg = config["scoring"]["mean_reversion"]
    rows = []
    for ticker, df in price_panel.items():
        if df.empty or len(df) < 25:
            continue
        close = df["close"]
        rsi_val = ind.rsi(close).iloc[-1]
        rsi_prev = ind.rsi(close).iloc[-2] if len(close) >= 2 else rsi_val
        bb = ind.bollinger_bands(close)
        bb_lower = bb["bb_lower"].iloc[-1]
        last_close = close.iloc[-1]
        prev_close = close.iloc[-2] if len(close) >= 2 else last_close

        # RSI oversold + rebound: low RSI, and current > previous (upturn)
        rsi_signal = 0.0
        if rsi_val < 35 and rsi_val > rsi_prev:
            rsi_signal = max(0.0, (35 - rsi_val) / 35 * 100)
            rsi_signal = min(100.0, rsi_signal + 50)  # boost if signal is active

        # BB lower touch+recover: previous close <= lower band, current above
        bb_signal = 0.0
        if pd.notna(bb_lower) and prev_close <= bb_lower and last_close > prev_close:
            bb_signal = 80.0

        rows.append({
            "ticker": ticker,
            "rsi": rsi_val,
            "rsi_signal": rsi_signal,
            "bb_signal": bb_signal,
        })

    out = pd.DataFrame(rows).set_index("ticker")
    if out.empty:
        return out

    w_rsi = cfg["rsi_oversold_weight"]
    w_bb = cfg["bb_lower_touch_weight"]
    wsum = w_rsi + w_bb

    out["mean_reversion_score"] = (out["rsi_signal"] * w_rsi + out["bb_signal"] * w_bb) / wsum
    return out


def detect_regime(kospi_ohlcv: pd.DataFrame) -> str:
    """Bull / Bear / Sideways based on KOSPI 200MA and 20MA slope."""
    if kospi_ohlcv.empty or len(kospi_ohlcv) < 200:
        return "sideways"
    close = kospi_ohlcv["close"]
    ma200 = ind.sma(close, 200).iloc[-1]
    ma20 = ind.sma(close, 20)
    last = close.iloc[-1]
    slope = ma20.iloc[-1] - ma20.iloc[-20] if len(ma20) > 20 else 0

    if last > ma200 and slope > 0:
        return "bull"
    if last < ma200 and slope < 0:
        return "bear"
    return "sideways"


def get_regime_weights(regime: str, config: dict) -> dict:
    base = {
        "momentum": config["scoring"]["factors"]["momentum"],
        "supply_demand": config["scoring"]["factors"]["supply_demand"],
        "quality": config["scoring"]["factors"]["quality"],
        "mean_reversion": config["scoring"]["factors"]["mean_reversion"],
    }
    regime_cfg = config.get("regime", {})
    if not regime_cfg.get("enabled", False):
        return base
    override = regime_cfg.get(regime, {}).get("weights_override")
    return override if override else base


def combine_scores(
    momentum: pd.DataFrame,
    supply: pd.DataFrame,
    quality: pd.DataFrame,
    reversion: pd.DataFrame,
    weights: dict,
) -> pd.DataFrame:
    """Combine factor scores into total (0-100) score."""
    frames = []
    if not momentum.empty:
        frames.append(momentum[["momentum_score"]])
    if not supply.empty:
        frames.append(supply[["supply_demand_score"]])
    if not quality.empty:
        frames.append(quality[["quality_score"]])
    if not reversion.empty:
        frames.append(reversion[["mean_reversion_score"]])

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, axis=1).fillna(0)

    wsum = sum(weights.values())
    total = (
        df.get("momentum_score", 0) * weights["momentum"]
        + df.get("supply_demand_score", 0) * weights["supply_demand"]
        + df.get("quality_score", 0) * weights["quality"]
        + df.get("mean_reversion_score", 0) * weights["mean_reversion"]
    ) / wsum
    df["total_score"] = total
    return df.sort_values("total_score", ascending=False)


def investment_amount_for_score(score: float, config: dict) -> int:
    """Return KRW investment size per score tier. 0 means don't buy."""
    for rule in config["investment_rules"]:
        if score >= rule["min_score"]:
            return int(rule["amount_krw"])
    return 0


# ============================================================
#  ABSOLUTE-THRESHOLD SCORING (new engine, used from 2026-04-24)
# ============================================================

def _abs_cfg(config: dict) -> dict:
    return config["scoring"]["absolute_thresholds"]


def compute_momentum_absolute(
    price_panel: dict[str, pd.DataFrame],
    benchmark_close: pd.Series,
    config: dict,
) -> pd.DataFrame:
    """Momentum factor via absolute thresholds.

    Scores each ticker on relative 5/20/60 day return vs KOSPI,
    applies absolute thresholds, combines with sub-weights.
    """
    m_thr = _abs_cfg(config)["momentum"]
    sub_cfg = config["scoring"]["momentum"]
    w5 = sub_cfg["return_5d_weight"]
    w20 = sub_cfg["return_20d_weight"]
    w60 = sub_cfg["return_60d_weight"]
    wsum = w5 + w20 + w60

    rows = []
    for ticker, df in price_panel.items():
        if df.empty or len(df) < 65:
            continue
        close = df["close"]
        r5 = ind.relative_return(close, benchmark_close, 5)
        r20 = ind.relative_return(close, benchmark_close, 20)
        r60 = ind.relative_return(close, benchmark_close, 60)

        if pd.isna(r20):
            continue
        s5 = threshold_score(r5 if pd.notna(r5) else 0, m_thr["rel_return_5d"])
        s20 = threshold_score(r20 if pd.notna(r20) else 0, m_thr["rel_return_20d"])
        s60 = threshold_score(r60 if pd.notna(r60) else 0, m_thr["rel_return_60d"])
        combined = (s5 * w5 + s20 * w20 + s60 * w60) / wsum

        rows.append({
            "ticker": ticker,
            "ret_5": r5, "ret_20": r20, "ret_60": r60,
            "momentum_score": combined,
        })

    return pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()


def compute_supply_demand_absolute(
    flows_panel: dict[str, pd.DataFrame],
    price_panel: dict[str, pd.DataFrame],
    market_cap: pd.Series,
    config: dict,
) -> pd.DataFrame:
    """Supply/Demand factor: foreign+institution net buying relative to market cap."""
    sd_thr = _abs_cfg(config)["supply_demand"]
    sub_cfg = config["scoring"]["supply_demand"]
    w_f = sub_cfg["foreign_5d_weight"]
    w_i = sub_cfg["institution_5d_weight"]
    w_c = sub_cfg["foreign_consecutive"]
    wsum = w_f + w_i + w_c

    rows = []
    foreign_candidates = ["외국인합계", "외국인"]
    inst_candidates = ["기관합계"]

    for ticker, df in flows_panel.items():
        if df.empty or len(df) < 5:
            continue
        f_col = _pick_col(df, foreign_candidates)
        i_col = _pick_col(df, inst_candidates)
        if not f_col:
            continue

        # Reference price for converting shares -> KRW (avg of last 5 closes)
        pdf = price_panel.get(ticker)
        if pdf is None or pdf.empty:
            continue
        avg_price = float(pdf["close"].tail(5).mean())
        cap = float(market_cap.get(ticker, 0) or 0)
        if cap <= 0 or avg_price <= 0:
            continue

        last5 = df.tail(5)
        foreign_5d_shares = float(last5[f_col].sum()) if f_col else 0.0
        inst_5d_shares = float(last5[i_col].sum()) if i_col else 0.0
        foreign_5d_krw = foreign_5d_shares * avg_price
        inst_5d_krw = inst_5d_shares * avg_price
        foreign_ratio = foreign_5d_krw / cap
        inst_ratio = inst_5d_krw / cap

        # consecutive foreign net-buy days
        consec = 0
        for v in reversed(df[f_col].tail(5).tolist()):
            if v > 0:
                consec += 1
            else:
                break

        s_f = threshold_score(foreign_ratio, sd_thr["foreign_5d_ratio"])
        s_i = threshold_score(inst_ratio, sd_thr["institution_5d_ratio"])
        s_c = threshold_score(consec, sd_thr["foreign_consecutive"])
        combined = (s_f * w_f + s_i * w_i + s_c * w_c) / wsum

        rows.append({
            "ticker": ticker,
            "foreign_ratio": foreign_ratio,
            "inst_ratio": inst_ratio,
            "foreign_consec": consec,
            "supply_demand_score": combined,
        })

    return pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()


def compute_quality_absolute(
    fundamentals: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """Quality factor: EY, ROE proxy, PBR inverse — absolute thresholds."""
    q_thr = _abs_cfg(config)["quality"]
    sub_cfg = config["scoring"]["quality"]
    w_roe = sub_cfg["roe_weight"]
    w_ey = sub_cfg["earnings_yield_weight"]
    w_debt = sub_cfg["debt_ratio_weight"]
    wsum = w_roe + w_ey + w_debt

    if fundamentals.empty:
        return pd.DataFrame()

    rows = []
    for ticker, row in fundamentals.iterrows():
        per = float(row.get("PER", 0) or 0)
        pbr = float(row.get("PBR", 0) or 0)
        eps = float(row.get("EPS", 0) or 0)
        bps = float(row.get("BPS", 0) or 0)

        ey = (1.0 / per) if per > 0 else None
        roe_proxy = (eps / bps) if (bps > 0 and eps > 0) else None
        pbr_inv = (1.0 / pbr) if pbr > 0 else None

        # Use only sub-metrics with actual data; renormalize weights.
        sub_scores = []
        sub_weights = []
        if ey is not None:
            sub_scores.append(threshold_score(ey, q_thr["earnings_yield"]))
            sub_weights.append(w_ey)
        if roe_proxy is not None:
            sub_scores.append(threshold_score(roe_proxy, q_thr["roe_proxy"]))
            sub_weights.append(w_roe)
        if pbr_inv is not None:
            sub_scores.append(threshold_score(pbr_inv, q_thr["pbr_inv"]))
            sub_weights.append(w_debt)

        if not sub_scores:
            combined = 50.0  # no data — neutral
        else:
            combined = sum(s * w for s, w in zip(sub_scores, sub_weights)) / sum(sub_weights)

        rows.append({
            "ticker": ticker,
            "earnings_yield": ey if ey is not None else 0,
            "roe_proxy": roe_proxy if roe_proxy is not None else 0,
            "pbr_inv": pbr_inv if pbr_inv is not None else 0,
            "quality_score": combined,
        })

    return pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()


def compute_mean_reversion_absolute(
    price_panel: dict[str, pd.DataFrame],
    config: dict,
) -> pd.DataFrame:
    """Mean reversion: RSI oversold + rebound, plus Bollinger lower bounce."""
    mr_thr = _abs_cfg(config)["mean_reversion"]
    sub_cfg = config["scoring"]["mean_reversion"]
    w_rsi = sub_cfg["rsi_oversold_weight"]
    w_bb = sub_cfg["bb_lower_touch_weight"]
    wsum = w_rsi + w_bb

    rows = []
    for ticker, df in price_panel.items():
        if df.empty or len(df) < 25:
            continue
        close = df["close"]
        rsi = ind.rsi(close)
        if len(rsi) < 2 or pd.isna(rsi.iloc[-1]):
            continue
        rsi_now = float(rsi.iloc[-1])
        rsi_prev = float(rsi.iloc[-2])

        # Rebounding RSI: bonus when signal present; otherwise neutral (no punish).
        if rsi_now < 50 and rsi_now > rsi_prev:
            s_rsi = threshold_score(rsi_now, mr_thr["rsi_when_rebounding"])
        else:
            s_rsi = 50.0  # neutral — absence of signal is not a negative

        bb = ind.bollinger_bands(close)
        bb_lower = bb["bb_lower"].iloc[-1]
        last_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        if pd.notna(bb_lower) and prev_close <= bb_lower and last_close > prev_close:
            s_bb = 90.0
        else:
            s_bb = 50.0  # neutral baseline

        combined = (s_rsi * w_rsi + s_bb * w_bb) / wsum

        rows.append({
            "ticker": ticker,
            "rsi": rsi_now,
            "mean_reversion_score": combined,
        })

    return pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()


def combine_scores_absolute(
    momentum: pd.DataFrame,
    supply: pd.DataFrame,
    quality: pd.DataFrame,
    reversion: pd.DataFrame,
    weights: dict,
    config: dict,
    universe_index: Optional[pd.Index] = None,
) -> pd.DataFrame:
    """Combine factor scores into base_score then apply top-of-day bonus.

    Output columns:
      momentum_score, supply_demand_score, quality_score, mean_reversion_score,
      base_score, top_bonus, total_score, amount_krw (set later)
    """
    # merge
    frames = []
    if not momentum.empty:
        frames.append(momentum[["momentum_score"]])
    if not supply.empty:
        frames.append(supply[["supply_demand_score"]])
    if not quality.empty:
        frames.append(quality[["quality_score"]])
    if not reversion.empty:
        frames.append(reversion[["mean_reversion_score"]])

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, axis=1)
    # Fill missing factor scores with 50 (neutral) so one missing factor doesn't zero the whole.
    df = df.fillna(50.0)

    wsum = sum(weights.values())
    df["base_score"] = (
        df.get("momentum_score", 50) * weights["momentum"]
        + df.get("supply_demand_score", 50) * weights["supply_demand"]
        + df.get("quality_score", 50) * weights["quality"]
        + df.get("mean_reversion_score", 50) * weights["mean_reversion"]
    ) / wsum

    # Top-of-day bonus
    bonus_cfg = config["scoring"].get("top_of_day_bonus", {})
    df["top_bonus"] = 0.0
    if bonus_cfg.get("enabled", False):
        min_base = float(bonus_cfg.get("min_base_score", 70))
        max_bonus = float(bonus_cfg.get("max_bonus", 10))
        top_p = float(bonus_cfg.get("top_percentile", 0.01))
        sec_p = float(bonus_cfg.get("secondary_percentile", 0.03))
        n = len(df)
        if n > 0:
            ranks = df["base_score"].rank(ascending=False, method="min")
            top_cut = max(1, int(n * top_p))
            sec_cut = max(1, int(n * sec_p))
            eligible = df["base_score"] >= min_base
            df.loc[(ranks <= top_cut) & eligible, "top_bonus"] = max_bonus
            # Secondary band gets half bonus
            df.loc[
                (ranks > top_cut) & (ranks <= sec_cut) & eligible,
                "top_bonus",
            ] = max_bonus / 2

    df["total_score"] = (df["base_score"] + df["top_bonus"]).clip(upper=100)
    return df.sort_values("total_score", ascending=False)
