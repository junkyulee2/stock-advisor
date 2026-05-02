"""Replay validation: would the new engine have picked winners over past 2-3 months?

Phase A-4/A-5 (2026-05-02). User asked: "Can't we just validate stocks
using the past 2-3 months of data and recommend them?"

Approach:
1. Pick N past dates spread over the last ~60 trading days
2. At each date D, run compute_daily_scores with the new weights
3. Take top-K picks where total_score >= MIN_SCORE
4. Track each pick's actual return from D to D+HOLD_DAYS (close-to-close)
5. Aggregate: mean return, win rate, vs KOSPI benchmark

LOOKAHEAD BIAS WARNING:
- Naver fundamentals are CURRENT snapshot only → value/quality factors
  use today's PER/PBR even for past dates
- get_net_purchases scraper returns latest flows → supply_demand also leaks
- Price-based factors (momentum, mean_rev, volatility, iqc_alpha) are clean
- Use results as DIRECTIONAL evidence, not literal expected returns. Real
  out-of-sample performance will be ~2-3%p lower (rough rule of thumb).
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import FinanceDataReader as fdr

# UTF-8 stdout on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import load_config
from run_daily import compute_daily_scores


NOW = datetime(2026, 4, 30)              # latest trading day available
SAMPLE_DAYS_BACK = list(range(10, 91, 5))  # 17 dates spread over 90 days
HOLD_DAYS = 22                            # ~1 calendar month
TOP_K = 15                                # picks per date (cap to bound runtime)
MIN_SCORE = 70                            # capture full distribution 70-100 for bin analysis
UNIVERSE_LIMIT = 500                      # match live config.universe.top_n_by_market_cap


def _kospi_return(start: str, end: str) -> float | None:
    """KOSPI index close-to-close return for the period."""
    try:
        df = fdr.DataReader("KS11", start, end)
        if df.empty or len(df) < 2:
            return None
        return float(df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
    except Exception:
        return None


def _forward_return(ticker: str, start: str, end: str) -> float | None:
    """Simple close-to-close return over the period (no sell rules applied)."""
    try:
        df = fdr.DataReader(ticker, start, end)
        if df.empty or len(df) < 2:
            return None
        return float(df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
    except Exception:
        return None


def _forward_return_with_sell_rules(
    ticker: str, start: str, end: str, sell_rules: dict
) -> tuple[float | None, str]:
    """Walk daily prices applying config.yaml sell_rules. Returns (realized_pct, exit_reason).

    Rules applied (in priority order on each day):
      1. Hard stop loss → -15% closes 100%
      2. Time stop → 20 trading days closes 100%
      3. Take profit partial → +20% closes 50% (continues with remainder)
      4. Trailing stop → -8% from peak closes remaining position
    Final hold period close → exit any remaining at last close.
    """
    try:
        df = fdr.DataReader(ticker, start, end)
        if df.empty or len(df) < 2:
            return None, "no_data"

        hi_lo = ("High" in df.columns and "Low" in df.columns)

        hard_stop = sell_rules["hard_stop_loss_pct"] / 100.0       # e.g. -0.15
        take_pct = sell_rules["hard_take_profit_partial_pct"] / 100.0
        take_ratio = sell_rules["hard_take_profit_partial_ratio"]
        trail_pct = sell_rules["trailing_stop_pct"] / 100.0        # e.g. -0.08
        time_stop = int(sell_rules["time_stop_days"])

        entry = float(df["Close"].iloc[0])
        position = 1.0
        realized = 0.0       # accumulated realized return contributions
        peak = entry         # tracked on CLOSE basis (production uses close)
        partial_taken = False

        # Iterate from day 1 (we entered at close of day 0). All checks use
        # daily CLOSE to match src/sell_signals.py production logic which
        # runs once per day on `current_price = close` and tracks
        # highest_price via close updates.
        for i in range(1, len(df)):
            close = float(df["Close"].iloc[i])

            # Time stop
            if i >= time_stop:
                realized += position * (close / entry - 1)
                return realized * 100.0, f"time_stop@{i}"

            # Hard stop on close
            ret_from_entry = close / entry - 1
            if ret_from_entry <= hard_stop:
                realized += position * ret_from_entry
                return realized * 100.0, f"hard_stop@{i}"

            # Take profit partial — first day close reaches +20%
            if not partial_taken and ret_from_entry >= take_pct:
                # Sell take_ratio at this close price
                realized += take_ratio * ret_from_entry
                position -= take_ratio
                partial_taken = True

            # Update peak on close
            if close > peak:
                peak = close

            # Trailing stop on remaining position (close vs close-peak)
            if position > 0 and close / peak - 1 <= trail_pct:
                realized += position * (close / entry - 1)
                return realized * 100.0, f"trailing@{i}"

        # End of period — exit any remaining at last close
        last_close = float(df["Close"].iloc[-1])
        realized += position * (last_close / entry - 1)
        return realized * 100.0, "period_end"
    except Exception:
        return None, "error"


def main():
    print("=" * 78)
    print("REPLAY VALIDATION — past picks vs actual forward returns")
    print(f"NOW={NOW:%Y-%m-%d}  HOLD={HOLD_DAYS}d  TOP_K={TOP_K}  MIN_SCORE={MIN_SCORE}")
    print("=" * 78)

    config = load_config()
    sell_rules = config["sell_rules"]
    print(f"Active weights: {config['scoring']['factors']}")
    print(f"Sell rules: hard_stop={sell_rules['hard_stop_loss_pct']}% "
          f"trailing={sell_rules['trailing_stop_pct']}% "
          f"take_profit_partial={sell_rules['hard_take_profit_partial_pct']}% "
          f"time_stop={sell_rules['time_stop_days']}d")
    print()

    all_picks = []
    bench_returns = []
    for days_back in SAMPLE_DAYS_BACK:
        asof_dt = NOW - timedelta(days=days_back)
        asof = asof_dt.strftime("%Y%m%d")
        end_dt = asof_dt + timedelta(days=int(HOLD_DAYS * 1.5))  # calendar buffer
        if end_dt > NOW:
            end_dt = NOW
        end = end_dt.strftime("%Y-%m-%d")
        start_iso = asof_dt.strftime("%Y-%m-%d")

        bench = _kospi_return(start_iso, end)
        bench_returns.append({"asof": asof, "kospi_return_pct": bench})

        print(f"\n--- {asof} (T-{days_back}) → {end} ---")
        t0 = time.time()
        try:
            df = compute_daily_scores(config, asof, limit=UNIVERSE_LIMIT)
        except Exception as e:
            print(f"  scoring failed: {e}")
            continue
        if df.empty:
            print("  empty scores")
            continue

        df_sorted = df.sort_values("total_score", ascending=False)
        picks = df_sorted[df_sorted["total_score"] >= MIN_SCORE].head(TOP_K)
        print(f"  scored {len(df)} tickers in {time.time()-t0:.1f}s | "
              f"{len(picks)} picks ≥{MIN_SCORE}")
        if picks.empty:
            print("  no picks above threshold this date")
            continue

        for ticker, row in picks.iterrows():
            naive_ret = _forward_return(str(ticker), start_iso, end)
            if naive_ret is None:
                continue
            sim_ret, exit_reason = _forward_return_with_sell_rules(
                str(ticker), start_iso, end, sell_rules
            )
            if sim_ret is None:
                sim_ret, exit_reason = naive_ret, "(naive fallback)"
            all_picks.append({
                "asof": asof,
                "days_back": days_back,
                "ticker": str(ticker),
                "name": str(row.get("name", "")),
                "score": float(row["total_score"]),
                "momentum": float(row.get("momentum_score", 0)),
                "value": float(row.get("value_score", 0)),
                "iqc_alpha": float(row.get("iqc_alpha_score", 0)),
                "naive_return_pct": naive_ret,
                "sim_return_pct": sim_ret,
                "exit_reason": exit_reason,
                "kospi_return_pct": bench,
                "alpha_pct": sim_ret - (bench or 0),
            })
            print(f"    {ticker} {row.get('name','')[:14]:<14} "
                  f"score={row['total_score']:5.1f}  "
                  f"naive={naive_ret:+6.2f}%  "
                  f"WITH_RULES={sim_ret:+6.2f}% [{exit_reason}]  "
                  f"vs KOSPI={(sim_ret - (bench or 0)):+5.2f}%")

    if not all_picks:
        print("\nNo picks collected.")
        return

    res = pd.DataFrame(all_picks)
    print("\n" + "=" * 78)
    print("AGGREGATE RESULTS")
    print("=" * 78)
    n = len(res)
    naive_mean = res["naive_return_pct"].mean()
    sim_mean = res["sim_return_pct"].mean()
    sim_median = res["sim_return_pct"].median()
    naive_win = (res["naive_return_pct"] > 0).mean() * 100
    sim_win = (res["sim_return_pct"] > 0).mean() * 100
    sim_alpha = res["alpha_pct"].mean()
    bench_mean = res["kospi_return_pct"].dropna().mean()

    print(f"  N picks tracked: {n}")
    print(f"  KOSPI mean:                 {bench_mean:+.2f}%")
    print(f"  Naive (no sell rules) mean: {naive_mean:+.2f}%   win {naive_win:.0f}%")
    print(f"  WITH SELL RULES mean:       {sim_mean:+.2f}%   win {sim_win:.0f}%   "
          f"(median {sim_median:+.2f}%)")
    print(f"  Mean alpha vs KOSPI:        {sim_alpha:+.2f}%   "
          f"(naive would be {naive_mean - bench_mean:+.2f}%)")
    print()
    print(f"  Exit reason breakdown:")
    for reason, cnt in res["exit_reason"].value_counts().items():
        print(f"    {reason:<20} {cnt}")
    print()
    print(f"  Best pick:  {res.loc[res['sim_return_pct'].idxmax(), ['asof','ticker','name','sim_return_pct','exit_reason']].to_dict()}")
    print(f"  Worst pick: {res.loc[res['sim_return_pct'].idxmin(), ['asof','ticker','name','sim_return_pct','exit_reason']].to_dict()}")

    # ---- Score-bin analysis (Phase A-5 threshold recalibration) ----
    print("\n" + "=" * 78)
    print("THRESHOLD ANALYSIS — return distribution by score bin")
    print("=" * 78)
    print(f"{'Bin':<10}{'N':>5}{'Win%':>8}{'Mean':>10}{'Median':>10}"
          f"{'KOSPI':>10}{'Alpha':>10}{'WorstRet':>11}")
    print("-" * 78)

    bins = [(95, 100), (90, 95), (85, 90), (82, 85), (80, 82), (75, 80), (70, 75)]
    for lo, hi in bins:
        sub = res[(res["score"] >= lo) & (res["score"] < hi)]
        if hi == 100:
            sub = res[(res["score"] >= lo) & (res["score"] <= hi)]
        if sub.empty:
            print(f"{lo}-{hi:<6}{0:>5}{'-':>8}{'-':>10}{'-':>10}{'-':>10}{'-':>10}{'-':>11}")
            continue
        n = len(sub)
        win = (sub["sim_return_pct"] > 0).mean() * 100
        mean = sub["sim_return_pct"].mean()
        med = sub["sim_return_pct"].median()
        kmean = sub["kospi_return_pct"].dropna().mean()
        alpha = mean - kmean
        worst = sub["sim_return_pct"].min()
        print(f"{lo}-{hi:<6}{n:>5}{win:>7.0f}%{mean:>+10.2f}{med:>+10.2f}"
              f"{kmean:>+10.2f}{alpha:>+10.2f}{worst:>+11.2f}")

    # Cumulative ≥ analysis (simulating "what if MIN_SCORE were X?")
    print("\n  Cumulative (≥ score threshold):")
    print(f"  {'≥ score':<10}{'N':>5}{'Win%':>8}{'Mean':>10}{'Alpha':>10}")
    print("  " + "-" * 43)
    for thresh in (70, 75, 80, 82, 85, 88, 90, 95):
        sub = res[res["score"] >= thresh]
        if sub.empty:
            continue
        n = len(sub)
        win = (sub["sim_return_pct"] > 0).mean() * 100
        mean = sub["sim_return_pct"].mean()
        kmean = sub["kospi_return_pct"].dropna().mean()
        alpha = mean - kmean
        marker = " ←" if alpha > 0 and n >= 5 else ""
        print(f"  ≥ {thresh:<8}{n:>5}{win:>7.0f}%{mean:>+10.2f}{alpha:>+10.2f}{marker}")

    # Save
    out_dir = PROJECT_ROOT / "data" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"replay_{NOW:%Y%m%d}.csv"
    res.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\nSaved: {out_path}")

    # Caveat reminder
    print()
    print("CAVEAT: value/quality/supply_demand factors leak today's snapshot")
    print("        into past dates. Real out-of-sample alpha likely 2-3%p lower.")


if __name__ == "__main__":
    main()
