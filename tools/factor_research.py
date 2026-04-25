"""Inductive factor research: which signals actually predicted future returns? # noqa

Approach:
1. Pick 2 lookback dates: T-30 and T-15 (calendar days)
2. For each top-cap ticker, compute features AS OF that lookback date
3. Compute forward return from that date to "now" (last trading day)
4. Measure each feature's predictive power:
   - Spearman rank IC (information coefficient)
   - Decile spread: top 10% return - bottom 10% return
5. Report → use to reweight the scoring engine

Limitations:
- Single observation per stock (not a full backtest, just a snapshot)
- 2026-04 market regime may differ from future
- Use as DIRECTIONAL evidence, not gospel
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# force UTF-8 stdout on Windows (CP949 default breaks unicode)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd
import FinanceDataReader as fdr

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

NOW_DATE = datetime(2026, 4, 24)         # last trading day (Friday)
LOOKBACK_DAYS = [30, 15]
TOP_N = 300                                # top by market cap
EXTRA_HISTORY = 130                        # ~60 trading days back (90 cal days too few w/ holidays)


def get_universe(top_n: int = TOP_N) -> list[str]:
    """Top N by market cap across KOSPI+KOSDAQ via FDR (no auth needed)."""
    df = fdr.StockListing("KRX")
    df = df.rename(columns={"Code": "ticker", "Marcap": "market_cap", "Market": "market"})
    df = df[df["market"].isin(["KOSPI", "KOSDAQ"])]
    df = df.sort_values("market_cap", ascending=False).head(top_n)
    return df["ticker"].astype(str).tolist()


def compute_features(close: pd.Series, asof_idx: int) -> dict | None:
    """Features computed using prices up to asof_idx (inclusive). Returns None if insufficient."""
    if asof_idx < 60:
        return None
    p = close.iloc[asof_idx]
    if p <= 0 or pd.isna(p):
        return None

    # Returns
    p_5 = close.iloc[asof_idx - 5]
    p_20 = close.iloc[asof_idx - 20]
    p_60 = close.iloc[asof_idx - 60]
    ret_5d = (p / p_5 - 1) if p_5 > 0 else np.nan
    ret_20d = (p / p_20 - 1) if p_20 > 0 else np.nan
    ret_60d = (p / p_60 - 1) if p_60 > 0 else np.nan

    # Moving averages
    ma20 = close.iloc[asof_idx - 19: asof_idx + 1].mean()
    ma60 = close.iloc[asof_idx - 59: asof_idx + 1].mean()
    ma20_dev = (p / ma20 - 1) if ma20 > 0 else np.nan
    ma60_dev = (p / ma60 - 1) if ma60 > 0 else np.nan

    # RSI(14)
    delta = close.iloc[max(0, asof_idx - 14): asof_idx + 1].diff()
    gain = delta.clip(lower=0).mean()
    loss = (-delta.clip(upper=0)).mean()
    rsi = 100 - 100 / (1 + (gain / loss)) if loss > 0 else 100.0

    # Volatility (20d std of daily returns)
    daily_ret = close.iloc[asof_idx - 19: asof_idx + 1].pct_change().dropna()
    vol_20d = float(daily_ret.std()) if len(daily_ret) > 5 else np.nan

    # Bollinger %B (20-day)
    if vol_20d and not np.isnan(vol_20d):
        bb_mean = close.iloc[asof_idx - 19: asof_idx + 1].mean()
        bb_std = close.iloc[asof_idx - 19: asof_idx + 1].std()
        bb_upper = bb_mean + 2 * bb_std
        bb_lower = bb_mean - 2 * bb_std
        if bb_upper > bb_lower:
            pct_b = (p - bb_lower) / (bb_upper - bb_lower)
        else:
            pct_b = 0.5
    else:
        pct_b = np.nan

    return {
        "ret_5d": ret_5d,
        "ret_20d": ret_20d,
        "ret_60d": ret_60d,
        "ma20_dev": ma20_dev,
        "ma60_dev": ma60_dev,
        "rsi": rsi,
        "vol_20d": vol_20d,
        "pct_b": pct_b,
    }


def collect_dataset(lookback_days: int) -> pd.DataFrame:
    asof = NOW_DATE - timedelta(days=lookback_days)
    fetch_start = asof - timedelta(days=EXTRA_HISTORY)

    print(f"\n=== Lookback {lookback_days}d : asof={asof:%Y-%m-%d}, now={NOW_DATE:%Y-%m-%d} ===")
    universe = get_universe(TOP_N)
    print(f"Universe: top {len(universe)} by market cap")

    rows = []
    t0 = time.time()
    for i, ticker in enumerate(universe):
        try:
            df = fdr.DataReader(ticker, fetch_start.strftime("%Y-%m-%d"),
                                NOW_DATE.strftime("%Y-%m-%d"))
            if df.empty or len(df) < 70:
                continue
            close = df["Close"].astype(float)

            # Find index closest to asof date
            asof_str = pd.Timestamp(asof)
            asof_idx = close.index.get_indexer([asof_str], method="nearest")[0]
            if asof_idx < 60 or asof_idx >= len(close) - 1:
                continue

            features = compute_features(close, asof_idx)
            if features is None:
                continue

            # Forward return: asof → last available
            p_asof = close.iloc[asof_idx]
            p_now = close.iloc[-1]
            forward_return = p_now / p_asof - 1

            features["ticker"] = ticker
            features["forward_return"] = forward_return
            rows.append(features)

            if (i + 1) % 50 == 0:
                print(f"  progress: {i+1}/{len(universe)} ({time.time()-t0:.1f}s)")
        except Exception:
            continue

    print(f"  collected {len(rows)} rows in {time.time()-t0:.1f}s")
    return pd.DataFrame(rows).set_index("ticker")


def analyze(df: pd.DataFrame, lookback_days: int) -> None:
    if df.empty:
        print("(empty)")
        return

    print(f"\n--- Forward {lookback_days}d return distribution ---")
    fr = df["forward_return"]
    print(f"  mean={fr.mean()*100:+.2f}%  median={fr.median()*100:+.2f}%  std={fr.std()*100:.2f}%")
    print(f"  min={fr.min()*100:+.1f}%  max={fr.max()*100:+.1f}%  n={len(fr)}")

    feature_cols = ["ret_5d", "ret_20d", "ret_60d", "ma20_dev", "ma60_dev", "rsi", "vol_20d", "pct_b"]

    print(f"\n--- Spearman IC (rank correlation with forward return) ---")
    print(f"{'feature':<12}{'IC':>8}{'sample':>10}")
    print("-" * 32)
    ic_results = {}
    for col in feature_cols:
        valid = df[[col, "forward_return"]].dropna()
        if len(valid) < 30:
            continue
        # Spearman = Pearson on ranks (avoid scipy dependency)
        ic = valid[col].rank().corr(valid["forward_return"].rank())
        ic_results[col] = ic
        sign = "++" if abs(ic) > 0.10 else ("+ " if abs(ic) > 0.05 else "  ")
        print(f"{col:<12}{ic:>+8.3f}{len(valid):>10} {sign}")

    print(f"\n--- Decile analysis (top 10% vs bottom 10% mean return) ---")
    print(f"{'feature':<12}{'D9 (top)':>12}{'D0 (bot)':>12}{'spread':>10}")
    print("-" * 46)
    for col in feature_cols:
        valid = df[[col, "forward_return"]].dropna()
        if len(valid) < 50:
            continue
        try:
            valid["dec"] = pd.qcut(valid[col].rank(method="first"), 10, labels=False)
            by_dec = valid.groupby("dec")["forward_return"].mean()
            d9 = by_dec.iloc[-1] * 100
            d0 = by_dec.iloc[0] * 100
            spread = d9 - d0
            mark = "<<" if abs(spread) > 5 else ""
            print(f"{col:<12}{d9:>+10.2f}% {d0:>+10.2f}% {spread:>+8.2f}% {mark}")
        except Exception as e:
            pass

    return ic_results


def main():
    print("=" * 70)
    print("FACTOR RESEARCH - what actually predicts future returns?")
    print("=" * 70)

    all_ic = {}
    all_dfs = {}
    for lb in LOOKBACK_DAYS:
        df = collect_dataset(lb)
        ic_results = analyze(df, lb)
        all_ic[lb] = ic_results
        all_dfs[lb] = df

    # Save raw data
    out_dir = PROJECT_ROOT / "data" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    for lb, df in all_dfs.items():
        path = out_dir / f"factor_data_{lb}d.csv"
        df.to_csv(path, encoding="utf-8")
        print(f"\nSaved: {path}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'feature':<12}{'IC 30d':>10}{'IC 15d':>10}")
    print("-" * 32)
    feature_cols = ["ret_5d", "ret_20d", "ret_60d", "ma20_dev", "ma60_dev", "rsi", "vol_20d", "pct_b"]
    for col in feature_cols:
        ic30 = all_ic.get(30, {}).get(col, np.nan)
        ic15 = all_ic.get(15, {}).get(col, np.nan)
        if not np.isnan(ic30) or not np.isnan(ic15):
            ic30_s = f"{ic30:+.3f}" if not np.isnan(ic30) else "  -  "
            ic15_s = f"{ic15:+.3f}" if not np.isnan(ic15) else "  -  "
            print(f"{col:<12}{ic30_s:>10}{ic15_s:>10}")


if __name__ == "__main__":
    main()
