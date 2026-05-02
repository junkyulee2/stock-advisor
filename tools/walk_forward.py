"""5-year walk-forward backtest.

Phase A 옵션 A (2026-05-02). 신규 7팩터 시스템(momentum/supply_demand/quality
/value/volatility/mean_reversion/iqc_alpha)을 60개월(5년) historical 데이터에
적용해서 진짜 alpha 측정.

방법:
  1. 5년 범위에서 매달 말 거래일을 샘플 (~60 dates)
  2. 각 날짜에서:
     - pykrx로 그 시점 KOSPI+KOSDAQ 시총 상위 200 universe 결정 (KRX 로그인 필요)
     - pykrx로 그 시점 fundamental (PER/PBR/EPS/BPS) 조회
     - FDR로 OHLCV 수집 (그 시점까지)
     - 7팩터 점수 계산 — supply_demand는 Naver 스크래퍼가 historical 미지원이므로 0 배정
     - 점수 ≥ 80 픽 (top 5)
  3. 각 픽을 22 거래일 hold + config sell rules 적용해서 realized return 측정
  4. Aggregate: 월별 포트폴리오 수익률 → 5y total, annualized Sharpe, MDD,
     KOSPI alpha. 그리고 팩터 ablation (각 팩터 가중치 0 시 결과)

전제:
  - KRX_ID / KRX_PW 환경변수 (GitHub Actions secret) 필수 — pykrx historical 데이터
  - GitHub Actions에서 실행. 로컬 PC에선 KRX 로그인 안 되어 있으면 의미 없음.

CAVEAT:
  - supply_demand 가중치 강제 0 → live system과 다른 가중치. live 결과와 직접 비교 불가.
  - AI veto 미적용 (Claude CLI 없음) — score 단독 결정.
  - 1개월 hold + sell rules. 월 단위 리밸런싱.

Run:
  python tools/walk_forward.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import FinanceDataReader as fdr

# UTF-8 stdout on Windows / CI
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import load_config, setup_logger
from src import scorer, indicators as ind

logger = setup_logger("walk_forward")

# ─── Backtest parameters ────────────────────────────────────────────
END_DATE = datetime(2026, 4, 30)
YEARS_BACK = 5
UNIVERSE_TOP_N = 200          # match live config
TOP_K_PICKS = 5               # picks per rebalance
MIN_SCORE = 80                # marginal threshold
HOLD_DAYS = 22                # trading days
PRICE_LOOKBACK = 80           # trading days needed for 60d momentum + MAs
DART_LOOKBACK = 30            # n/a for backtest (no AI), kept for symmetry


def _month_ends(start_dt: datetime, end_dt: datetime) -> list[datetime]:
    """Return last-business-day of each month between start and end (inclusive)."""
    out = []
    d = datetime(start_dt.year, start_dt.month, 1)
    while d <= end_dt:
        next_month = datetime(d.year + (1 if d.month == 12 else 0),
                              1 if d.month == 12 else d.month + 1, 1)
        last = next_month - timedelta(days=1)
        # Snap to weekday (Mon-Fri)
        while last.weekday() >= 5:
            last -= timedelta(days=1)
        if start_dt <= last <= end_dt:
            out.append(last)
        d = next_month
    return out


def _historical_universe(asof: str) -> list[str]:
    """Top N tickers by market cap at asof date via pykrx. Returns 6-digit codes.

    Falls back to current FDR snapshot if pykrx (KRX login) fails — biased
    but workable.
    """
    try:
        from pykrx import stock as kx
        frames = []
        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = kx.get_market_cap_by_ticker(asof, market=market)
                if df is not None and not df.empty:
                    df = df.copy()
                    df["market"] = market
                    frames.append(df)
            except Exception as e:
                logger.warning(f"pykrx market_cap {market} {asof} failed: {e}")
        if frames:
            cap = pd.concat(frames)
            cap = cap.sort_values("시가총액", ascending=False).head(UNIVERSE_TOP_N)
            return [str(t).zfill(6) for t in cap.index]
    except Exception as e:
        logger.warning(f"pykrx universe fetch failed: {e}")

    # Fallback: current snapshot (lookahead-biased but maintains pipeline)
    logger.warning(f"falling back to current universe snapshot for {asof}")
    listing = fdr.StockListing("KRX")
    listing = listing.rename(columns={"Code": "ticker", "Marcap": "market_cap",
                                      "Market": "market"})
    listing = listing[listing["market"].isin(["KOSPI", "KOSDAQ"])]
    listing = listing.sort_values("market_cap", ascending=False).head(UNIVERSE_TOP_N)
    return listing["ticker"].astype(str).str.zfill(6).tolist()


def _historical_fundamentals(asof: str) -> pd.DataFrame:
    """All-tickers fundamental snapshot at asof via pykrx."""
    try:
        from pykrx import stock as kx
        frames = []
        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = kx.get_market_fundamental(asof, market=market)
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception as e:
                logger.warning(f"pykrx fundamental {market} {asof} failed: {e}")
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames)
        out.index.name = "ticker"
        out.index = out.index.astype(str).str.zfill(6)
        return out
    except Exception as e:
        logger.warning(f"pykrx fundamental fetch failed: {e}")
        return pd.DataFrame()


def _fetch_ohlcv_panel(tickers: list[str], asof_dt: datetime) -> dict[str, pd.DataFrame]:
    """Build {ticker: ohlcv df} ending at asof_dt with PRICE_LOOKBACK trading days back."""
    start_dt = asof_dt - timedelta(days=int(PRICE_LOOKBACK * 1.5) + 30)
    start = start_dt.strftime("%Y-%m-%d")
    end = asof_dt.strftime("%Y-%m-%d")
    panel = {}
    for t in tickers:
        try:
            df = fdr.DataReader(t, start, end)
            if df is None or df.empty or len(df) < 70:
                continue
            df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                    "Close": "close", "Volume": "volume",
                                    "Change": "change_pct"})
            panel[t] = df
        except Exception:
            continue
    return panel


def _kospi_close(asof_dt: datetime, lookback_days: int = 80) -> pd.Series:
    start = (asof_dt - timedelta(days=int(lookback_days * 1.5) + 30)).strftime("%Y-%m-%d")
    end = asof_dt.strftime("%Y-%m-%d")
    df = fdr.DataReader("KS11", start, end)
    if df.empty:
        return pd.Series(dtype=float)
    return df["Close"].astype(float)


def _score_at_date(config: dict, asof_dt: datetime) -> pd.DataFrame:
    """Run the 7-factor engine at a historical date. Returns scored df.

    supply_demand factor is forced to 0 weight (no historical Naver flows).
    """
    asof = asof_dt.strftime("%Y%m%d")

    # Universe
    tickers = _historical_universe(asof)
    if not tickers:
        return pd.DataFrame()

    # Price panel
    price_panel = _fetch_ohlcv_panel(tickers, asof_dt)
    if not price_panel:
        return pd.DataFrame()

    # KOSPI for momentum
    kospi = _kospi_close(asof_dt, lookback_days=PRICE_LOOKBACK)
    if kospi.empty:
        return pd.DataFrame()

    # Fundamentals
    fund_full = _historical_fundamentals(asof)
    fundamentals = fund_full.loc[fund_full.index.intersection(price_panel.keys())] \
                    if not fund_full.empty else pd.DataFrame()

    # Compute each factor (skip supply_demand — historical unavailable)
    mom = scorer.compute_momentum_absolute(price_panel, kospi, config)
    qual = scorer.compute_quality_absolute(fundamentals, config)
    rev = scorer.compute_mean_reversion_absolute(price_panel, config)
    vol = scorer.compute_volatility_absolute(price_panel, config)
    val = scorer.compute_value_absolute(fundamentals, config)
    iqc = scorer.compute_iqc_combined_absolute(price_panel, config)

    # Override weights to skip supply_demand (set its weight to 0)
    factors = config["scoring"]["factors"]
    weights = {
        "momentum":       factors["momentum"],
        "supply_demand":  0,           # ZERO for backtest (no historical flows)
        "quality":        factors["quality"],
        "mean_reversion": factors["mean_reversion"],
        "volatility":     factors.get("volatility", 0),
        "value":          factors.get("value", 0),
        "iqc_alpha":      factors.get("iqc_alpha", 0),
    }

    combined = scorer.combine_scores_absolute(
        mom,
        pd.DataFrame(),  # empty supply
        qual, rev, weights, config,
        volatility=vol, value=val, iqc_alpha=iqc,
    )
    return combined


def _simulate_hold(ticker: str, entry_dt: datetime, sell_rules: dict
                   ) -> tuple[float | None, str]:
    """Hold ticker for HOLD_DAYS trading days from entry_dt with sell rules.
    Returns (realized_pct, exit_reason)."""
    end_dt = entry_dt + timedelta(days=int(HOLD_DAYS * 1.5) + 7)
    try:
        df = fdr.DataReader(ticker, entry_dt.strftime("%Y-%m-%d"),
                            end_dt.strftime("%Y-%m-%d"))
    except Exception:
        return None, "fetch_error"
    if df.empty or len(df) < 2:
        return None, "no_data"

    hard_stop = sell_rules["hard_stop_loss_pct"] / 100.0
    take_pct = sell_rules["hard_take_profit_partial_pct"] / 100.0
    take_ratio = sell_rules["hard_take_profit_partial_ratio"]
    trail_pct = sell_rules["trailing_stop_pct"] / 100.0
    time_stop = int(sell_rules["time_stop_days"])

    entry = float(df["Close"].iloc[0])
    position = 1.0
    realized = 0.0
    peak = entry
    partial_taken = False

    for i in range(1, len(df)):
        close = float(df["Close"].iloc[i])
        if i >= time_stop:
            realized += position * (close / entry - 1)
            return realized * 100.0, f"time@{i}"
        ret = close / entry - 1
        if ret <= hard_stop:
            realized += position * ret
            return realized * 100.0, f"hard@{i}"
        if not partial_taken and ret >= take_pct:
            realized += take_ratio * ret
            position -= take_ratio
            partial_taken = True
        if close > peak:
            peak = close
        if position > 0 and close / peak - 1 <= trail_pct:
            realized += position * ret
            return realized * 100.0, f"trail@{i}"

    realized += position * (float(df["Close"].iloc[-1]) / entry - 1)
    return realized * 100.0, "period_end"


def _kospi_period_return(start: datetime, end: datetime) -> float | None:
    try:
        df = fdr.DataReader("KS11", start.strftime("%Y-%m-%d"),
                            end.strftime("%Y-%m-%d"))
        if df.empty or len(df) < 2:
            return None
        return float(df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
    except Exception:
        return None


def _annualized_sharpe(monthly_returns: pd.Series) -> float:
    if monthly_returns.empty or monthly_returns.std() == 0:
        return 0.0
    return float(monthly_returns.mean() / monthly_returns.std() * np.sqrt(12))


def _max_drawdown(cumulative: pd.Series) -> float:
    if cumulative.empty:
        return 0.0
    peak = cumulative.cummax()
    dd = (cumulative - peak) / peak
    return float(dd.min())


def main():
    config = load_config()
    sell_rules = config["sell_rules"]
    start_dt = END_DATE - timedelta(days=YEARS_BACK * 365 + 10)

    print("=" * 78)
    print(f"5-YEAR WALK-FORWARD: {start_dt:%Y-%m-%d} → {END_DATE:%Y-%m-%d}")
    print(f"Universe top_n={UNIVERSE_TOP_N}  TOP_K={TOP_K_PICKS}  "
          f"MIN_SCORE={MIN_SCORE}  HOLD={HOLD_DAYS}d")
    print("=" * 78)

    dates = _month_ends(start_dt, END_DATE)
    print(f"Rebalance dates: {len(dates)}")
    print()

    all_picks = []
    monthly_summary = []
    t_start = time.time()

    for i, asof_dt in enumerate(dates):
        t0 = time.time()
        try:
            scored = _score_at_date(config, asof_dt)
        except Exception as e:
            logger.warning(f"score failed at {asof_dt:%Y-%m-%d}: {e}")
            continue

        if scored.empty:
            print(f"[{i+1:2d}/{len(dates)}] {asof_dt:%Y-%m-%d}  no scores")
            continue

        scored = scored.sort_values("total_score", ascending=False)
        picks = scored[scored["total_score"] >= MIN_SCORE].head(TOP_K_PICKS)
        n_eligible = len(scored[scored["total_score"] >= MIN_SCORE])

        if picks.empty:
            print(f"[{i+1:2d}/{len(dates)}] {asof_dt:%Y-%m-%d}  "
                  f"top_score={scored['total_score'].max():.1f}  no picks ≥{MIN_SCORE}")
            monthly_summary.append({
                "date": asof_dt.strftime("%Y-%m-%d"),
                "n_picks": 0, "portfolio_return_pct": 0.0,
                "kospi_return_pct": 0.0, "alpha_pct": 0.0,
            })
            continue

        # Forward simulate each pick
        pick_returns = []
        for ticker in picks.index:
            ret, reason = _simulate_hold(str(ticker), asof_dt, sell_rules)
            if ret is None:
                continue
            row = picks.loc[ticker]
            pick_returns.append(ret)
            all_picks.append({
                "date": asof_dt.strftime("%Y-%m-%d"),
                "ticker": str(ticker),
                "score": float(row["total_score"]),
                "momentum": float(row.get("momentum_score", 0)),
                "value": float(row.get("value_score", 0)),
                "iqc_alpha": float(row.get("iqc_alpha_score", 0)),
                "volatility": float(row.get("volatility_score", 0)),
                "return_pct": ret,
                "exit_reason": reason,
            })

        # Equal-weight portfolio return
        port_ret = sum(pick_returns) / len(pick_returns) if pick_returns else 0.0
        end_dt = asof_dt + timedelta(days=int(HOLD_DAYS * 1.5))
        if end_dt > END_DATE:
            end_dt = END_DATE
        bench = _kospi_period_return(asof_dt, end_dt) or 0.0

        monthly_summary.append({
            "date": asof_dt.strftime("%Y-%m-%d"),
            "n_picks": len(pick_returns),
            "portfolio_return_pct": port_ret,
            "kospi_return_pct": bench,
            "alpha_pct": port_ret - bench,
        })

        print(f"[{i+1:2d}/{len(dates)}] {asof_dt:%Y-%m-%d}  "
              f"picks={len(pick_returns)}/{n_eligible}≥{MIN_SCORE}  "
              f"port={port_ret:+6.2f}%  KOSPI={bench:+6.2f}%  "
              f"α={port_ret - bench:+6.2f}%  ({time.time()-t0:.1f}s)")

    print()
    print(f"runtime: {(time.time() - t_start) / 60:.1f} min")

    if not monthly_summary:
        print("No monthly results.")
        return

    monthly = pd.DataFrame(monthly_summary)
    monthly["date"] = pd.to_datetime(monthly["date"])
    monthly = monthly.set_index("date").sort_index()

    # Cumulative
    monthly["port_cum"] = (1 + monthly["portfolio_return_pct"] / 100).cumprod()
    monthly["kospi_cum"] = (1 + monthly["kospi_return_pct"] / 100).cumprod()

    # Aggregate stats
    n_months = len(monthly)
    mean_port = monthly["portfolio_return_pct"].mean()
    mean_kospi = monthly["kospi_return_pct"].mean()
    cum_port = monthly["port_cum"].iloc[-1] - 1
    cum_kospi = monthly["kospi_cum"].iloc[-1] - 1
    sharpe = _annualized_sharpe(monthly["portfolio_return_pct"] / 100)
    sharpe_kospi = _annualized_sharpe(monthly["kospi_return_pct"] / 100)
    mdd = _max_drawdown(monthly["port_cum"])
    mdd_kospi = _max_drawdown(monthly["kospi_cum"])
    win_rate = (monthly["portfolio_return_pct"] > monthly["kospi_return_pct"]).mean() * 100
    months_no_pick = (monthly["n_picks"] == 0).sum()

    print()
    print("=" * 78)
    print("AGGREGATE — 5y walk-forward")
    print("=" * 78)
    print(f"  Months evaluated:        {n_months}  ({months_no_pick} with no picks)")
    print(f"  Total return:            port={cum_port*100:+7.2f}%  "
          f"KOSPI={cum_kospi*100:+7.2f}%  α={cum_port*100 - cum_kospi*100:+7.2f}%")
    print(f"  Annualized return:       port={((1+cum_port)**(12/n_months)-1)*100:+6.2f}%  "
          f"KOSPI={((1+cum_kospi)**(12/n_months)-1)*100:+6.2f}%")
    print(f"  Monthly mean:            port={mean_port:+5.2f}%  KOSPI={mean_kospi:+5.2f}%  "
          f"α={mean_port-mean_kospi:+5.2f}%")
    print(f"  Annualized Sharpe:       port={sharpe:5.2f}  KOSPI={sharpe_kospi:5.2f}")
    print(f"  Max drawdown:            port={mdd*100:+6.2f}%  KOSPI={mdd_kospi*100:+6.2f}%")
    print(f"  Months port > KOSPI:     {win_rate:.0f}%")

    # Save
    out_dir = PROJECT_ROOT / "data" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")

    monthly.reset_index().to_csv(out_dir / f"walk_forward_monthly_{stamp}.csv",
                                 index=False, encoding="utf-8")
    pd.DataFrame(all_picks).to_csv(out_dir / f"walk_forward_picks_{stamp}.csv",
                                   index=False, encoding="utf-8")

    summary = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "params": {
            "end_date": END_DATE.strftime("%Y-%m-%d"),
            "years_back": YEARS_BACK,
            "universe_top_n": UNIVERSE_TOP_N,
            "top_k_picks": TOP_K_PICKS,
            "min_score": MIN_SCORE,
            "hold_days": HOLD_DAYS,
        },
        "weights_used": {
            "momentum": config["scoring"]["factors"]["momentum"],
            "supply_demand": 0,  # forced 0 for backtest
            "quality": config["scoring"]["factors"]["quality"],
            "value": config["scoring"]["factors"].get("value", 0),
            "volatility": config["scoring"]["factors"].get("volatility", 0),
            "mean_reversion": config["scoring"]["factors"]["mean_reversion"],
            "iqc_alpha": config["scoring"]["factors"].get("iqc_alpha", 0),
        },
        "results": {
            "n_months": n_months,
            "months_no_pick": int(months_no_pick),
            "cum_return_pct": round(cum_port * 100, 2),
            "cum_kospi_pct": round(cum_kospi * 100, 2),
            "cum_alpha_pct": round((cum_port - cum_kospi) * 100, 2),
            "annualized_return_pct": round(((1 + cum_port) ** (12 / n_months) - 1) * 100, 2),
            "monthly_mean_pct": round(mean_port, 2),
            "monthly_alpha_pct": round(mean_port - mean_kospi, 2),
            "annualized_sharpe": round(sharpe, 2),
            "max_drawdown_pct": round(mdd * 100, 2),
            "win_rate_vs_kospi_pct": round(win_rate, 1),
        },
    }
    (out_dir / f"walk_forward_summary_{stamp}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(f"Saved: {out_dir}/walk_forward_*_{stamp}.{{csv,json}}")


if __name__ == "__main__":
    main()
