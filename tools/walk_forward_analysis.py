"""5-year walk-forward analysis — IC, factor lenses, weight optimization.

Phase A 옵션 B deep-dive (2026-05-02). 이전 walk-forward 결과(-143%p alpha)
의 진짜 원인을 데이터로 파헤치고 가중치 재조정 방향 도출.

분석 단계:
  1. 팩터별 IC (Spearman 랭크 상관) + decile spread — 어떤 팩터가 진짜 신호?
  2. IQC alpha 단독 전략 — 상위 20% iqc_alpha만 픽한다면?
  3. Value 단독 전략 — 상위 20% value만 픽한다면?
  4. IQC × Value 교집합 — 둘 다 강한 종목은?
  5. 가중치 그리드 서치 — 어떤 조합이 5y Sharpe 극대화하는가?
  6. Top-K 효과 — 분산도(top 5 vs top 10 vs top 20)별 결과
  7. 권고: 새 가중치 후보 셋

전제: data/research/walk_forward_factor_panel_*.csv 존재 (walk_forward.py 출력)

Run:
  python tools/walk_forward_analysis.py
"""
from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_panel() -> pd.DataFrame:
    out_dir = PROJECT_ROOT / "data" / "research"
    panels = sorted(out_dir.glob("walk_forward_factor_panel_*.csv"))
    if not panels:
        raise FileNotFoundError(
            f"No walk_forward_factor_panel_*.csv in {out_dir}. "
            "Run tools/walk_forward.py first (or trigger workflow_dispatch)."
        )
    latest = panels[-1]
    print(f"Loading panel: {latest.name}")
    df = pd.read_csv(latest)
    df["date"] = pd.to_datetime(df["date"])
    return df


def section_header(t: str):
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


# ─── 1. Per-factor IC ────────────────────────────────────────────────

def factor_ic(df: pd.DataFrame, factors: list[str]) -> pd.DataFrame:
    """Spearman IC per factor over the full panel + per-month avg IC."""
    full_ic = {}
    monthly_ics = {f: [] for f in factors}

    # Per-date IC (cross-sectional per month) → average
    for date, sub in df.groupby("date"):
        if len(sub) < 30:
            continue
        for f in factors:
            valid = sub[[f, "fwd_return_pct"]].dropna()
            if len(valid) < 20:
                continue
            ic = valid[f].rank().corr(valid["fwd_return_pct"].rank())
            if pd.notna(ic):
                monthly_ics[f].append(ic)

    rows = []
    for f in factors:
        ics = monthly_ics[f]
        if not ics:
            rows.append({"factor": f, "n_months": 0, "ic_mean": np.nan,
                         "ic_std": np.nan, "ic_ir": np.nan, "ic_pct_pos": np.nan})
            continue
        s = pd.Series(ics)
        rows.append({
            "factor": f,
            "n_months": len(s),
            "ic_mean": round(float(s.mean()), 3),
            "ic_std": round(float(s.std()), 3),
            "ic_ir": round(float(s.mean() / s.std() * np.sqrt(12)), 2) if s.std() > 0 else 0,
            "ic_pct_pos": round(float((s > 0).mean() * 100), 1),
        })
    return pd.DataFrame(rows).sort_values("ic_mean", ascending=False)


# ─── 2. Single-factor decile portfolios ─────────────────────────────

def decile_portfolio(df: pd.DataFrame, factor: str, deciles: int = 5) -> pd.DataFrame:
    """For each month, sort by factor and form decile/quintile portfolios.
    Return mean forward return per decile."""
    rows = []
    for date, sub in df.groupby("date"):
        valid = sub[[factor, "fwd_return_pct"]].dropna()
        if len(valid) < deciles * 5:
            continue
        try:
            valid = valid.copy()
            valid["q"] = pd.qcut(valid[factor].rank(method="first"), deciles,
                                 labels=False, duplicates="drop")
        except Exception:
            continue
        for q, sub2 in valid.groupby("q"):
            rows.append({"date": date, "q": int(q),
                         "ret": float(sub2["fwd_return_pct"].mean())})
    if not rows:
        return pd.DataFrame()
    g = pd.DataFrame(rows).groupby("q")["ret"].agg(["mean", "std", "count"]).reset_index()
    g.columns = ["quintile", "mean_pct", "std_pct", "n"]
    g["sharpe"] = g["mean_pct"] / g["std_pct"] * np.sqrt(12)
    return g


# ─── 3. Subset strategies ───────────────────────────────────────────

def subset_strategy(df: pd.DataFrame, condition: pd.Series, label: str,
                    top_k: int = 5) -> dict:
    """For picks satisfying `condition`, take top-K by total_score per month
    (or by passing-factor when no total_score). Compute backtest stats."""
    sub = df[condition].copy()
    if sub.empty:
        return {"label": label, "n_months": 0, "n_picks": 0,
                "monthly_mean": 0, "ann_sharpe": 0, "alpha_vs_kospi": np.nan}

    monthly_returns = []
    for date, group in sub.groupby("date"):
        picks = group.nlargest(top_k, "total_score")
        if picks.empty:
            continue
        port_ret = picks["fwd_return_pct"].mean()
        monthly_returns.append(port_ret)

    if not monthly_returns:
        return {"label": label, "n_months": 0, "n_picks": 0,
                "monthly_mean": 0, "ann_sharpe": 0, "alpha_vs_kospi": np.nan}

    s = pd.Series(monthly_returns) / 100.0
    cum = (1 + s).cumprod().iloc[-1] - 1
    ann = ((1 + cum) ** (12 / len(s)) - 1) * 100 if len(s) else 0
    sharpe = float(s.mean() / s.std() * np.sqrt(12)) if s.std() > 0 else 0
    return {
        "label": label,
        "n_months": len(monthly_returns),
        "n_picks": len(sub),
        "monthly_mean_pct": round(float(s.mean() * 100), 2),
        "cum_return_pct": round(cum * 100, 2),
        "annualized_pct": round(ann, 2),
        "annualized_sharpe": round(sharpe, 2),
    }


# ─── 4. Weight grid search ──────────────────────────────────────────

FACTORS = ["momentum", "quality", "value", "volatility", "mean_reversion", "iqc_alpha"]


def evaluate_weights(df: pd.DataFrame, weights: dict, top_k: int = 5,
                     min_score: float = 80) -> dict:
    """Apply given weights to the panel, simulate top-K monthly picks."""
    wsum = sum(weights.values())
    if wsum <= 0:
        return {"ann_sharpe": -99, "ann_pct": -99}

    # Compute composite per row
    df = df.copy()
    composite = sum(df[f] * weights.get(f, 0) for f in FACTORS) / wsum
    df["composite"] = composite

    monthly_returns = []
    for date, group in df.groupby("date"):
        picks = group[group["composite"] >= min_score].nlargest(top_k, "composite")
        if picks.empty:
            monthly_returns.append(0.0)
            continue
        monthly_returns.append(picks["fwd_return_pct"].mean())

    if not monthly_returns:
        return {"ann_sharpe": -99, "ann_pct": -99}

    s = pd.Series(monthly_returns) / 100.0
    cum = (1 + s).cumprod().iloc[-1] - 1
    n = len(s)
    ann = ((1 + cum) ** (12 / n) - 1) * 100 if n else 0
    sharpe = float(s.mean() / s.std() * np.sqrt(12)) if s.std() > 0 else 0
    mdd = float(((1 + s).cumprod() / (1 + s).cumprod().cummax() - 1).min() * 100)
    return {
        "weights": weights,
        "n_months": n,
        "monthly_mean_pct": round(float(s.mean() * 100), 2),
        "cum_return_pct": round(cum * 100, 2),
        "annualized_pct": round(ann, 2),
        "annualized_sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(mdd, 2),
        "win_pct": round(float((s > 0).mean() * 100), 1),
    }


def grid_search(df: pd.DataFrame, top_k: int = 5, min_score: float = 80,
                step: int = 5) -> pd.DataFrame:
    """Coarse grid: each factor weight in {0, 5, 10, 15, 20, 25, 30}.
    Total weight normalized; only test subset of combinations."""
    candidates = []

    # Smart sampling — only generate combos summing to "reasonable" totals.
    # 6 factors × 7 levels = 117,649 combos — too many. Sample with rules:
    # at least 3 factors active, total weight 70-110 (will be normalized).
    levels = list(range(0, 31, step))
    n_tested = 0
    seen_keys = set()
    for combo in product(levels, repeat=len(FACTORS)):
        total = sum(combo)
        if total < 60 or total > 120:
            continue
        active = sum(1 for v in combo if v > 0)
        if active < 3:
            continue
        # Normalize to sum 100 for canonical comparison
        norm = tuple(round(v * 100 / total) for v in combo)
        key = norm
        if key in seen_keys:
            continue
        seen_keys.add(key)
        weights = dict(zip(FACTORS, norm))
        result = evaluate_weights(df, weights, top_k=top_k, min_score=min_score)
        candidates.append(result)
        n_tested += 1
    print(f"  grid search evaluated {n_tested} combinations")
    return pd.DataFrame([c for c in candidates if "weights" in c])


# ─── 5. Main ────────────────────────────────────────────────────────

def main():
    df = _load_panel()
    print(f"Panel: {len(df)} rows × {len(df.columns)} cols")
    print(f"Date range: {df['date'].min():%Y-%m-%d} → {df['date'].max():%Y-%m-%d}")
    print(f"Unique tickers: {df['ticker'].nunique()}")
    print(f"Months: {df['date'].nunique()}")
    print(f"Forward return mean (raw, all rows): {df['fwd_return_pct'].mean():+.2f}%")

    # ─── 1. Per-factor IC ─────────────────────────────────────────
    section_header("1. Per-factor IC (Spearman rank correlation, monthly avg)")
    ic = factor_ic(df, FACTORS)
    print(ic.to_string(index=False))
    print()
    print("기준: |IC mean| > 0.05 → 의미 있는 신호")
    print("     IC IR > 0.5 → 안정적 신호 (월별 부호 일관)")

    # ─── 2. Decile/quintile portfolios per factor ──────────────────
    section_header("2. Quintile portfolios per factor (Q4 = top 20%, Q0 = bottom 20%)")
    print(f"{'factor':<16}{'Q0':>9}{'Q1':>9}{'Q2':>9}{'Q3':>9}{'Q4':>9}{'spread':>9}")
    print("-" * 70)
    for f in FACTORS:
        d = decile_portfolio(df, f, deciles=5)
        if d.empty:
            print(f"{f:<16} (insufficient data)")
            continue
        means = d.set_index("quintile")["mean_pct"]
        spread = means.iloc[-1] - means.iloc[0]
        print(f"{f:<16}"
              f"{means.iloc[0]:>+8.2f}%{means.iloc[1]:>+8.2f}%{means.iloc[2]:>+8.2f}%"
              f"{means.iloc[3]:>+8.2f}%{means.iloc[4]:>+8.2f}%{spread:>+8.2f}%")

    # ─── 3. Subset strategies ──────────────────────────────────────
    section_header("3. 단일/조합 팩터 전략 — 5y backtest (top 5 monthly, eq-weight)")
    strategies = [
        ("All universe", df["total_score"] >= 0),
        ("total_score >= 80", df["total_score"] >= 80),
        ("total_score >= 85", df["total_score"] >= 85),
        ("value >= 80", df["value"] >= 80),
        ("value >= 90", df["value"] >= 90),
        ("iqc_alpha >= 80", df["iqc_alpha"] >= 80),
        ("iqc_alpha >= 90", df["iqc_alpha"] >= 90),
        ("value >= 70 AND iqc_alpha >= 70", (df["value"] >= 70) & (df["iqc_alpha"] >= 70)),
        ("value >= 80 AND momentum >= 80", (df["value"] >= 80) & (df["momentum"] >= 80)),
        ("vol >= 80", df["volatility"] >= 80),
        ("vol >= 80 AND momentum >= 80", (df["volatility"] >= 80) & (df["momentum"] >= 80)),
        ("momentum <= 30 AND value >= 80", (df["momentum"] <= 30) & (df["value"] >= 80)),
    ]
    print(f"{'strategy':<40}{'months':>8}{'mean':>9}{'cum':>9}{'ann':>8}{'Sharpe':>8}")
    print("-" * 82)
    for label, cond in strategies:
        r = subset_strategy(df, cond, label, top_k=5)
        if r["n_months"] == 0:
            print(f"{label[:39]:<40}{'(no months)':>8}")
            continue
        print(f"{label[:39]:<40}{r['n_months']:>8}"
              f"{r['monthly_mean_pct']:>+8.2f}%{r['cum_return_pct']:>+8.2f}%"
              f"{r['annualized_pct']:>+7.2f}%{r['annualized_sharpe']:>8.2f}")

    # ─── 4. Top-K sensitivity ──────────────────────────────────────
    section_header("4. Top-K 분산 효과 (현재 시스템 가중치 그대로, MIN_SCORE=80)")
    weights_current = {"momentum": 30, "quality": 10, "value": 10,
                       "volatility": 10, "mean_reversion": 10, "iqc_alpha": 5}
    print(f"{'top_k':>6}{'months':>8}{'mean':>9}{'cum':>9}{'ann':>8}{'Sharpe':>8}{'MDD':>9}")
    print("-" * 60)
    for k in (1, 3, 5, 10, 20):
        r = evaluate_weights(df, weights_current, top_k=k, min_score=80)
        if "n_months" not in r:
            continue
        print(f"{k:>6}{r['n_months']:>8}{r['monthly_mean_pct']:>+8.2f}%"
              f"{r['cum_return_pct']:>+8.2f}%{r['annualized_pct']:>+7.2f}%"
              f"{r['annualized_sharpe']:>8.2f}{r['max_drawdown_pct']:>+8.2f}%")

    # ─── 5. Weight grid search ─────────────────────────────────────
    section_header("5. 가중치 그리드 서치 (top 5, min_score 80, in-sample 최적)")
    grid = grid_search(df, top_k=5, min_score=80, step=5)
    if grid.empty:
        print("(no candidates)")
    else:
        # Top by Sharpe
        top_sharpe = grid.sort_values("annualized_sharpe", ascending=False).head(10)
        print("\n● Sharpe 상위 10:")
        for _, r in top_sharpe.iterrows():
            w = r["weights"]
            wstr = " ".join(f"{k[:3]}={v}" for k, v in w.items() if v > 0)
            print(f"  Sharpe={r['annualized_sharpe']:5.2f}  "
                  f"ann={r['annualized_pct']:+7.2f}%  "
                  f"MDD={r['max_drawdown_pct']:+6.1f}%  | {wstr}")

        # Top by annualized return
        top_ret = grid.sort_values("annualized_pct", ascending=False).head(10)
        print("\n● 연환산 수익률 상위 10:")
        for _, r in top_ret.iterrows():
            w = r["weights"]
            wstr = " ".join(f"{k[:3]}={v}" for k, v in w.items() if v > 0)
            print(f"  ann={r['annualized_pct']:+7.2f}%  "
                  f"Sharpe={r['annualized_sharpe']:5.2f}  "
                  f"MDD={r['max_drawdown_pct']:+6.1f}%  | {wstr}")

        # Best single recommendation: balance Sharpe + return + MDD
        # composite score: ann*0.5 + sharpe*30 - |mdd|*0.3
        grid["composite"] = (grid["annualized_pct"] * 0.5
                             + grid["annualized_sharpe"] * 30
                             - grid["max_drawdown_pct"].abs() * 0.3)
        best = grid.nlargest(1, "composite").iloc[0]
        section_header("★ 추천 가중치 (Sharpe + 수익 + MDD 균형)")
        print(f"  ann return:   {best['annualized_pct']:+.2f}%")
        print(f"  Sharpe:       {best['annualized_sharpe']:.2f}")
        print(f"  MDD:          {best['max_drawdown_pct']:+.2f}%")
        print(f"  cum return:   {best['cum_return_pct']:+.2f}%")
        print(f"  win rate:     {best['win_pct']:.1f}%")
        print(f"  weights: {best['weights']}")

        # Save full grid
        out_dir = PROJECT_ROOT / "data" / "research"
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d")
        export = grid.copy()
        export["weights_str"] = export["weights"].apply(lambda d: json.dumps(d, ensure_ascii=False))
        export.drop(columns=["weights"]).to_csv(
            out_dir / f"walk_forward_grid_search_{stamp}.csv",
            index=False, encoding="utf-8",
        )
        # Summary
        summary = {
            "n_combinations": len(grid),
            "best_by_composite": {
                "weights": best["weights"],
                "annualized_pct": float(best["annualized_pct"]),
                "annualized_sharpe": float(best["annualized_sharpe"]),
                "max_drawdown_pct": float(best["max_drawdown_pct"]),
                "cum_return_pct": float(best["cum_return_pct"]),
            },
            "current_weights_baseline": {
                **weights_current,
                "result": evaluate_weights(df, weights_current, top_k=5, min_score=80),
            },
        }
        # strip nested dicts that aren't json-serializable
        if "weights" in summary["current_weights_baseline"]["result"]:
            del summary["current_weights_baseline"]["result"]["weights"]

        (out_dir / f"walk_forward_grid_summary_{stamp}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nSaved: data/research/walk_forward_grid_*_{stamp}.{{csv,json}}")


if __name__ == "__main__":
    main()
