"""5-year walk-forward backtest (optimized).

Phase A 옵션 A (2026-05-02). 신규 7팩터 시스템 5년 historical 검증.

방법:
  1. 현재 시총 상위 200 universe (survivorship bias 인정 — caveat 문서화)
  2. 각 ticker의 5년 OHLCV를 사전 한 번에 fetch (parallel) → 메모리 캐시
  3. 매월 말 거래일에:
     - 캐시된 panel을 그 시점까지 slice
     - pykrx로 그 시점 fundamental 조회 (KRX 로그인 필요)
     - 7팩터 스코어 계산 (supply_demand 가중치 0 — historical 미지원)
     - 점수 ≥ MIN_SCORE 픽 top K
     - 22일 hold + sell rules로 realized return
  4. Aggregate: 월별 portfolio return → cumulative, Sharpe, MDD, KOSPI alpha

CAVEAT:
  - Survivorship bias: 현재 top 200 사용 → 5년 전 망한 종목 빠짐 → 결과 over-estimate
  - supply_demand 가중치 강제 0 → live와 다른 weight
  - AI veto 미적용 (Claude CLI 부재)

Run (local):  python tools/walk_forward.py
Run (CI):     gh workflow run walk_forward.yml
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import FinanceDataReader as fdr

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import load_config, setup_logger
from src import scorer

logger = setup_logger("walk_forward")

# ─── Backtest parameters ────────────────────────────────────────────
END_DATE = datetime(2026, 4, 30)
YEARS_BACK = 5
UNIVERSE_TOP_N = 200
TOP_K_PICKS = 5
MIN_SCORE = 80
HOLD_DAYS = 22
PRICE_LOOKBACK_NEEDED = 80
PARALLEL_WORKERS = 20


def _month_ends(start_dt: datetime, end_dt: datetime) -> list[datetime]:
    out = []
    d = datetime(start_dt.year, start_dt.month, 1)
    while d <= end_dt:
        nxt = datetime(d.year + (1 if d.month == 12 else 0),
                       1 if d.month == 12 else d.month + 1, 1)
        last = nxt - timedelta(days=1)
        while last.weekday() >= 5:
            last -= timedelta(days=1)
        if start_dt <= last <= end_dt:
            out.append(last)
        d = nxt
    return out


def _current_universe() -> list[str]:
    """Current top N by market cap. Survivorship-biased for backtest — caveat noted."""
    listing = fdr.StockListing("KRX")
    listing = listing.rename(columns={"Code": "ticker", "Marcap": "market_cap",
                                      "Market": "market"})
    listing = listing[listing["market"].isin(["KOSPI", "KOSDAQ"])]
    listing = listing.sort_values("market_cap", ascending=False).head(UNIVERSE_TOP_N)
    return listing["ticker"].astype(str).str.zfill(6).tolist()


def _prefetch_ohlcv(tickers: list[str], start_dt: datetime, end_dt: datetime
                    ) -> dict[str, pd.DataFrame]:
    """Fetch each ticker's full backtest-range OHLCV ONCE in parallel."""
    start = start_dt.strftime("%Y-%m-%d")
    end = end_dt.strftime("%Y-%m-%d")
    panels: dict[str, pd.DataFrame] = {}
    t0 = time.time()

    def fetch(t: str):
        try:
            df = fdr.DataReader(t, start, end)
            if df is None or df.empty:
                return t, None
            df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                    "Close": "close", "Volume": "volume",
                                    "Change": "change_pct"})
            return t, df
        except Exception:
            return t, None

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
        futures = [ex.submit(fetch, t) for t in tickers]
        completed = 0
        for fut in as_completed(futures):
            t, df = fut.result()
            if df is not None:
                panels[t] = df
            completed += 1
            if completed % 50 == 0:
                logger.info(f"  OHLCV {completed}/{len(tickers)} ({time.time()-t0:.1f}s)")
    logger.info(f"OHLCV pre-fetch: {len(panels)}/{len(tickers)} in {time.time()-t0:.1f}s")
    return panels


def _slice_panel(panels: dict[str, pd.DataFrame], asof_dt: datetime
                 ) -> dict[str, pd.DataFrame]:
    """Cut each ticker's panel to data ≤ asof_dt, last PRICE_LOOKBACK_NEEDED+5 rows."""
    out = {}
    cutoff = pd.Timestamp(asof_dt)
    for t, df in panels.items():
        sliced = df[df.index <= cutoff]
        if len(sliced) < 65:
            continue
        out[t] = sliced.tail(PRICE_LOOKBACK_NEEDED + 5)
    return out


_FUND_CACHE: dict[str, pd.DataFrame] = {}


def _pykrx_fundamentals(asof: str) -> pd.DataFrame:
    if asof in _FUND_CACHE:
        return _FUND_CACHE[asof]
    try:
        from pykrx import stock as kx
        frames = []
        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = kx.get_market_fundamental(asof, market=market)
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception as e:
                logger.warning(f"pykrx fund {market} {asof}: {e}")
        if not frames:
            out = pd.DataFrame()
        else:
            out = pd.concat(frames)
            out.index.name = "ticker"
            out.index = out.index.astype(str).str.zfill(6)
    except Exception as e:
        logger.warning(f"pykrx fund import/run fail {asof}: {e}")
        out = pd.DataFrame()
    _FUND_CACHE[asof] = out
    return out


def _kospi_close_for(asof_dt: datetime, kospi_full: pd.Series) -> pd.Series:
    cutoff = pd.Timestamp(asof_dt)
    return kospi_full[kospi_full.index <= cutoff].tail(PRICE_LOOKBACK_NEEDED + 5)


def _score_at_date(config: dict, asof_dt: datetime,
                   panels_full: dict[str, pd.DataFrame],
                   kospi_full: pd.Series) -> pd.DataFrame:
    asof = asof_dt.strftime("%Y%m%d")
    panel = _slice_panel(panels_full, asof_dt)
    if not panel:
        return pd.DataFrame()
    kospi = _kospi_close_for(asof_dt, kospi_full)
    if kospi.empty:
        return pd.DataFrame()

    fund_full = _pykrx_fundamentals(asof)
    fundamentals = fund_full.loc[fund_full.index.intersection(panel.keys())] \
                    if not fund_full.empty else pd.DataFrame()

    mom = scorer.compute_momentum_absolute(panel, kospi, config)
    qual = scorer.compute_quality_absolute(fundamentals, config)
    rev = scorer.compute_mean_reversion_absolute(panel, config)
    vol = scorer.compute_volatility_absolute(panel, config)
    val = scorer.compute_value_absolute(fundamentals, config)
    iqc = scorer.compute_iqc_combined_absolute(panel, config)

    factors = config["scoring"]["factors"]
    weights = {
        "momentum":       factors["momentum"],
        "supply_demand":  0,
        "quality":        factors["quality"],
        "mean_reversion": factors["mean_reversion"],
        "volatility":     factors.get("volatility", 0),
        "value":          factors.get("value", 0),
        "iqc_alpha":      factors.get("iqc_alpha", 0),
    }
    return scorer.combine_scores_absolute(
        mom, pd.DataFrame(), qual, rev, weights, config,
        volatility=vol, value=val, iqc_alpha=iqc,
    )


def _simulate_hold(panel_df: pd.DataFrame, sell_rules: dict
                   ) -> tuple[float | None, str]:
    """Use the cached OHLCV slice from entry forward."""
    if panel_df.empty or len(panel_df) < 2:
        return None, "no_data"

    hard_stop = sell_rules["hard_stop_loss_pct"] / 100.0
    take_pct = sell_rules["hard_take_profit_partial_pct"] / 100.0
    take_ratio = sell_rules["hard_take_profit_partial_ratio"]
    trail_pct = sell_rules["trailing_stop_pct"] / 100.0
    time_stop = int(sell_rules["time_stop_days"])

    entry = float(panel_df["close"].iloc[0])
    position = 1.0
    realized = 0.0
    peak = entry
    partial_taken = False

    n = min(len(panel_df), time_stop + 1)
    for i in range(1, n):
        close = float(panel_df["close"].iloc[i])
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

    last_close = float(panel_df["close"].iloc[-1])
    realized += position * (last_close / entry - 1)
    return realized * 100.0, "period_end"


def _annualized_sharpe(monthly: pd.Series) -> float:
    if monthly.empty or monthly.std() == 0:
        return 0.0
    return float(monthly.mean() / monthly.std() * np.sqrt(12))


def _max_drawdown(cum: pd.Series) -> float:
    if cum.empty:
        return 0.0
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return float(dd.min())


def main():
    config = load_config()
    sell_rules = config["sell_rules"]
    end_dt = END_DATE
    start_dt = end_dt - timedelta(days=YEARS_BACK * 365 + 10)
    fetch_start = start_dt - timedelta(days=int(PRICE_LOOKBACK_NEEDED * 1.5 + 30))

    print("=" * 78)
    print(f"5-YEAR WALK-FORWARD: {start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}")
    print(f"Universe top_n={UNIVERSE_TOP_N}  TOP_K={TOP_K_PICKS}  "
          f"MIN_SCORE={MIN_SCORE}  HOLD={HOLD_DAYS}d")
    print("=" * 78)

    print("\n[1/3] Building universe + pre-fetching 5y OHLCV…")
    tickers = _current_universe()
    print(f"  universe: {len(tickers)}")

    # Fetch full range — fetch_start to end_dt + HOLD buffer
    panels = _prefetch_ohlcv(
        tickers, fetch_start, end_dt + timedelta(days=int(HOLD_DAYS * 1.5))
    )
    print(f"  OHLCV panels ready: {len(panels)} tickers")

    print("\n[2/3] Pre-fetching KOSPI benchmark…")
    kospi_full = fdr.DataReader(
        "KS11",
        fetch_start.strftime("%Y-%m-%d"),
        (end_dt + timedelta(days=int(HOLD_DAYS * 1.5))).strftime("%Y-%m-%d"),
    )["Close"].astype(float)
    print(f"  KOSPI bars: {len(kospi_full)}")

    dates = _month_ends(start_dt, end_dt)
    print(f"\n[3/3] Running monthly walk-forward across {len(dates)} dates…")
    print()

    all_picks = []
    monthly_summary = []
    t_start = time.time()

    for i, asof_dt in enumerate(dates):
        t0 = time.time()
        try:
            scored = _score_at_date(config, asof_dt, panels, kospi_full)
        except Exception as e:
            logger.warning(f"score fail {asof_dt:%Y-%m-%d}: {e}")
            continue
        if scored.empty:
            print(f"[{i+1:2d}/{len(dates)}] {asof_dt:%Y-%m-%d}  no scores")
            continue

        scored = scored.sort_values("total_score", ascending=False)
        picks = scored[scored["total_score"] >= MIN_SCORE].head(TOP_K_PICKS)
        n_eligible = len(scored[scored["total_score"] >= MIN_SCORE])

        # KOSPI period return
        end_idx_kospi = kospi_full[kospi_full.index <= pd.Timestamp(asof_dt)].index
        if len(end_idx_kospi) == 0:
            bench = 0.0
        else:
            entry_kospi = float(kospi_full.loc[end_idx_kospi[-1]])
            future_kospi = kospi_full[kospi_full.index > pd.Timestamp(asof_dt)].head(HOLD_DAYS)
            if len(future_kospi) == 0:
                bench = 0.0
            else:
                bench = (float(future_kospi.iloc[-1]) / entry_kospi - 1) * 100

        if picks.empty:
            top_score = float(scored["total_score"].max())
            print(f"[{i+1:2d}/{len(dates)}] {asof_dt:%Y-%m-%d}  "
                  f"top={top_score:.1f}  no picks ≥{MIN_SCORE}  KOSPI={bench:+5.2f}%")
            monthly_summary.append({
                "date": asof_dt.strftime("%Y-%m-%d"),
                "n_picks": 0, "portfolio_return_pct": 0.0,
                "kospi_return_pct": bench, "alpha_pct": -bench,
            })
            continue

        # Forward sim
        pick_returns = []
        for ticker in picks.index:
            tk = str(ticker)
            full_panel = panels.get(tk)
            if full_panel is None:
                continue
            forward = full_panel[full_panel.index > pd.Timestamp(asof_dt)].head(HOLD_DAYS + 1)
            entry_row = full_panel[full_panel.index <= pd.Timestamp(asof_dt)].tail(1)
            if entry_row.empty or forward.empty:
                continue
            sim_panel = pd.concat([entry_row, forward])
            ret, reason = _simulate_hold(sim_panel, sell_rules)
            if ret is None:
                continue
            row = picks.loc[ticker]
            pick_returns.append(ret)
            all_picks.append({
                "date": asof_dt.strftime("%Y-%m-%d"),
                "ticker": tk,
                "score": float(row["total_score"]),
                "momentum": float(row.get("momentum_score", 0)),
                "value": float(row.get("value_score", 0)),
                "iqc_alpha": float(row.get("iqc_alpha_score", 0)),
                "volatility": float(row.get("volatility_score", 0)),
                "return_pct": ret,
                "exit_reason": reason,
            })

        port_ret = sum(pick_returns) / len(pick_returns) if pick_returns else 0.0
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

    print(f"\nLoop runtime: {(time.time() - t_start)/60:.1f} min")

    if not monthly_summary:
        print("No results.")
        return

    monthly = pd.DataFrame(monthly_summary)
    monthly["date"] = pd.to_datetime(monthly["date"])
    monthly = monthly.set_index("date").sort_index()
    monthly["port_cum"] = (1 + monthly["portfolio_return_pct"] / 100).cumprod()
    monthly["kospi_cum"] = (1 + monthly["kospi_return_pct"] / 100).cumprod()

    n_months = len(monthly)
    months_no_pick = int((monthly["n_picks"] == 0).sum())
    mean_port = float(monthly["portfolio_return_pct"].mean())
    mean_kospi = float(monthly["kospi_return_pct"].mean())
    cum_port = float(monthly["port_cum"].iloc[-1] - 1)
    cum_kospi = float(monthly["kospi_cum"].iloc[-1] - 1)
    sharpe = _annualized_sharpe(monthly["portfolio_return_pct"] / 100)
    sharpe_kospi = _annualized_sharpe(monthly["kospi_return_pct"] / 100)
    mdd = _max_drawdown(monthly["port_cum"])
    mdd_kospi = _max_drawdown(monthly["kospi_cum"])
    win_rate = float((monthly["portfolio_return_pct"] > monthly["kospi_return_pct"]).mean() * 100)
    annualized = ((1 + cum_port) ** (12 / n_months) - 1) * 100 if n_months else 0
    annualized_kospi = ((1 + cum_kospi) ** (12 / n_months) - 1) * 100 if n_months else 0

    print()
    print("=" * 78)
    print("AGGREGATE — 5y walk-forward (universe survivorship-biased)")
    print("=" * 78)
    print(f"  Months evaluated:        {n_months}  ({months_no_pick} with no picks)")
    print(f"  Total return:            port={cum_port*100:+7.2f}%  "
          f"KOSPI={cum_kospi*100:+7.2f}%  α={(cum_port-cum_kospi)*100:+7.2f}%")
    print(f"  Annualized return:       port={annualized:+6.2f}%  KOSPI={annualized_kospi:+6.2f}%")
    print(f"  Monthly mean:            port={mean_port:+5.2f}%  KOSPI={mean_kospi:+5.2f}%  "
          f"α={mean_port-mean_kospi:+5.2f}%")
    print(f"  Annualized Sharpe:       port={sharpe:5.2f}  KOSPI={sharpe_kospi:5.2f}")
    print(f"  Max drawdown:            port={mdd*100:+6.2f}%  KOSPI={mdd_kospi*100:+6.2f}%")
    print(f"  Months port > KOSPI:     {win_rate:.0f}%")

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
            "end_date": end_dt.strftime("%Y-%m-%d"),
            "years_back": YEARS_BACK,
            "universe_top_n": UNIVERSE_TOP_N,
            "top_k_picks": TOP_K_PICKS,
            "min_score": MIN_SCORE,
            "hold_days": HOLD_DAYS,
            "supply_demand_weight_forced_to": 0,
            "ai_veto_applied": False,
            "universe_survivorship_bias": True,
        },
        "results": {
            "n_months": n_months,
            "months_no_pick": months_no_pick,
            "cum_return_pct": round(cum_port * 100, 2),
            "cum_kospi_pct": round(cum_kospi * 100, 2),
            "cum_alpha_pct": round((cum_port - cum_kospi) * 100, 2),
            "annualized_return_pct": round(annualized, 2),
            "annualized_kospi_pct": round(annualized_kospi, 2),
            "monthly_mean_pct": round(mean_port, 2),
            "monthly_alpha_pct": round(mean_port - mean_kospi, 2),
            "annualized_sharpe": round(sharpe, 2),
            "annualized_sharpe_kospi": round(sharpe_kospi, 2),
            "max_drawdown_pct": round(mdd * 100, 2),
            "max_drawdown_kospi_pct": round(mdd_kospi * 100, 2),
            "win_rate_vs_kospi_pct": round(win_rate, 1),
        },
    }
    (out_dir / f"walk_forward_summary_{stamp}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nSaved: data/research/walk_forward_*_{stamp}.{{csv,json}}")


if __name__ == "__main__":
    main()
