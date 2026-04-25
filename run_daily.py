"""Daily orchestrator: compute scores and sell signals, save, notify.

Usage:
  python run_daily.py [--mode scores|signals|both]

Typical schedule:
  - Evening (after market close): scores for next morning's picks
  - Morning / intraday: signals for held positions
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd

from src import data_collector as dc
from src import scorer
from src import sell_signals as ss
from src import portfolio as pf
from src import notifier
from src.utils import (
    PROJECT_ROOT,
    iso_today,
    load_config,
    load_json,
    previous_trading_day,
    save_json,
    setup_logger,
)

logger = setup_logger("run_daily")


def fetch_universe_and_data(config: dict, as_of: str, limit: int | None = None):
    """Pull top-N universe and build per-ticker price & flow panels."""
    u_cfg = config["universe"]
    top_n = limit if limit else u_cfg["top_n_by_market_cap"]
    universe = dc.get_universe(
        as_of=as_of,
        markets=u_cfg["markets"],
        top_n=top_n,
    )
    logger.info(f"universe: {len(universe)} tickers as of {as_of}")

    # Liquidity filter
    min_tv = u_cfg["filters"]["min_avg_trading_value_krw"]
    universe = universe[universe["trading_value"] >= min_tv].reset_index(drop=True)
    logger.info(f"after liquidity filter: {len(universe)} tickers")

    # Force-include held positions so the web app can always show a current
    # score even when a held ticker slips below top_n or the liquidity floor.
    try:
        portfolio = pf.load_portfolio(PROJECT_ROOT / config["paths"]["portfolio"])
        held = set(portfolio.get("positions", {}).keys())
    except Exception:
        held = set()
    missing = held - set(universe["ticker"])
    if missing:
        full_listing = dc.get_universe(
            as_of=as_of, markets=u_cfg["markets"], top_n=10_000,
        )
        add = full_listing[full_listing["ticker"].isin(missing)]
        if not add.empty:
            universe = pd.concat([universe, add], ignore_index=True).drop_duplicates(
                subset=["ticker"], keep="first"
            ).reset_index(drop=True)
            logger.info(f"force-included {len(add)} held tickers not in top_n/liquidity")

    # Price panel — 80 trading days back for 60d momentum + MAs
    start, end = dc.date_range_for_lookback(as_of, 80)
    tickers = universe["ticker"].tolist()

    price_panel = _parallel_fetch(
        tickers,
        worker=lambda t: dc.get_ohlcv(t, start, end),
        label="OHLCV",
        max_workers=15,
    )

    # Flow panel (foreign/institution from Naver). Cached daily.
    flows_panel = _parallel_fetch(
        tickers,
        worker=lambda t: dc.get_net_purchases(t),
        label="flows",
        max_workers=12,
    )

    # Fundamentals — per-ticker Naver main page. Cached weekly.
    fund_rows = _parallel_fetch(
        tickers,
        worker=lambda t: dc.get_fundamental(t),
        label="fundamentals",
        max_workers=12,
        keep_falsy=True,
    )
    fundamentals = _fund_dict_to_df(fund_rows)

    # Benchmark
    kospi = dc.get_kospi_ohlcv(start, end)

    return universe, price_panel, flows_panel, fundamentals, kospi


def _parallel_fetch(
    tickers: list,
    worker,
    label: str,
    max_workers: int = 15,
    keep_falsy: bool = False,
) -> dict:
    """Run `worker(ticker)` across tickers in parallel. Skips empty results."""
    results: dict = {}
    total = len(tickers)
    completed = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(worker, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                r = fut.result()
                if keep_falsy:
                    if r is not None:
                        results[t] = r
                else:
                    if r is not None and (not hasattr(r, "empty") or not r.empty):
                        results[t] = r
            except Exception as e:
                logger.debug(f"{label} fetch fail {t}: {e}")
            completed += 1
            if completed % 100 == 0 or completed == total:
                elapsed = time.time() - t0
                logger.info(f"{label}: {completed}/{total} done ({elapsed:.1f}s)")
    elapsed = time.time() - t0
    logger.info(f"{label} complete: {len(results)}/{total} successful in {elapsed:.1f}s")
    return results


def _fund_dict_to_df(fund_rows: dict) -> pd.DataFrame:
    """Convert {ticker: {per, pbr, eps, bps, roe}} -> df indexed by ticker
    with uppercase cols for scorer compatibility."""
    if not fund_rows:
        return pd.DataFrame()
    records = []
    for t, f in fund_rows.items():
        records.append({
            "ticker": t,
            "PER": f.get("per") or 0,
            "PBR": f.get("pbr") or 0,
            "EPS": f.get("eps") or 0,
            "BPS": 0,
            "DIV": 0,
            "DPS": 0,
        })
    return pd.DataFrame(records).set_index("ticker")


def compute_daily_scores(config: dict, as_of: str, limit: int | None = None) -> pd.DataFrame:
    universe, price_panel, flows_panel, fundamentals, kospi = fetch_universe_and_data(
        config, as_of, limit=limit
    )

    regime = scorer.detect_regime(kospi)
    weights = scorer.get_regime_weights(regime, config)
    logger.info(f"regime: {regime} | weights: {weights}")

    market_cap_s = universe.set_index("ticker")["market_cap"]

    # ABSOLUTE-threshold scoring (new engine, 2026-04-24).
    # Each factor scores against fixed thresholds so 95 means absolute strength.
    # A small top-of-day bonus lets ordinary markets still surface picks.
    mom = scorer.compute_momentum_absolute(price_panel, kospi["close"], config)
    sup = scorer.compute_supply_demand_absolute(flows_panel, price_panel, market_cap_s, config)
    qual = scorer.compute_quality_absolute(fundamentals, config)
    rev = scorer.compute_mean_reversion_absolute(price_panel, config)
    vol = scorer.compute_volatility_absolute(price_panel, config)

    combined = scorer.combine_scores_absolute(
        mom, sup, qual, rev, weights, config, volatility=vol,
    )
    if combined.empty:
        return combined

    combined = combined.merge(
        universe[["ticker", "name", "market", "close", "market_cap"]].set_index("ticker"),
        left_index=True, right_index=True, how="left",
    )
    combined["regime"] = regime
    combined["as_of"] = as_of
    combined["amount_krw"] = combined["total_score"].apply(
        lambda s: scorer.investment_amount_for_score(s, config)
    )
    return combined


def save_daily_scores(df: pd.DataFrame, config: dict, as_of: str) -> Path:
    out_dir = PROJECT_ROOT / config["paths"]["scores_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"scores_{as_of}.json"
    df.reset_index().to_json(out_path, orient="records", force_ascii=False, indent=2)
    logger.info(f"saved scores -> {out_path}")
    cleanup_old_scores(out_dir, keep=7)
    return out_path


def cleanup_old_scores(scores_dir: Path, keep: int = 7) -> None:
    """Delete all but the most recent `keep` scores_*.json files.

    Held positions remain fully tracked via portfolio.json (entry_score) and
    today's file — old daily snapshots are not needed for day-to-day use.
    """
    files = sorted(scores_dir.glob("scores_*.json"))
    if len(files) <= keep:
        return
    for f in files[:-keep]:
        try:
            f.unlink()
            logger.info(f"pruned old scores: {f.name}")
        except Exception as e:
            logger.warning(f"failed to prune {f.name}: {e}")


def recommend_top3(df: pd.DataFrame, config: dict) -> list[dict]:
    min_score = config["portfolio_limits"]["min_score_to_buy"]
    top = df[df["total_score"] >= min_score].head(3)
    picks = []
    for tick, row in top.iterrows():
        picks.append({
            "ticker": tick,
            "name": row.get("name", ""),
            "total_score": float(row["total_score"]),
            "amount_krw": int(row.get("amount_krw", 0)),
            "close": float(row.get("close", 0)),
            "factors": {
                "momentum": float(row.get("momentum_score", 0)),
                "supply": float(row.get("supply_demand_score", 0)),
                "quality": float(row.get("quality_score", 0)),
                "reversion": float(row.get("mean_reversion_score", 0)),
            },
        })
    return picks


def check_sell_signals(config: dict, as_of: str) -> list[dict]:
    """Scan held positions for sell signals."""
    portfolio = pf.load_portfolio(PROJECT_ROOT / config["paths"]["portfolio"])
    if not portfolio["positions"]:
        logger.info("no open positions; skip signal check")
        return []

    # Fetch OHLCV and flows for held tickers only
    start, end = dc.date_range_for_lookback(as_of, 25)
    alerts = []
    for ticker, pos in list(portfolio["positions"].items()):
        try:
            price_df = dc.get_ohlcv(ticker, start, end)
            flows_df = dc.get_net_purchases(ticker, start, end)
        except Exception as e:
            logger.warning(f"fetch failed for {ticker}: {e}")
            continue
        if price_df.empty:
            continue
        current_price = float(price_df["close"].iloc[-1])
        pf.update_highest(portfolio, ticker, current_price)

        decision = ss.decide_exit(pos, price_df, flows_df, current_price, config)
        if decision:
            alerts.append({
                "ticker": ticker,
                "position": pos,
                "current_price": current_price,
                "decision": decision,
            })

    # Save updated highest_price
    save_json(PROJECT_ROOT / config["paths"]["portfolio"], portfolio)
    return alerts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["scores", "signals", "both"], default="both")
    parser.add_argument("--as-of", default=None, help="YYYYMMDD, defaults to previous trading day")
    parser.add_argument("--limit", type=int, default=None, help="Override universe size (for testing)")
    args = parser.parse_args()

    config = load_config()
    as_of = args.as_of or previous_trading_day()
    logger.info(f"run_daily start | mode={args.mode} | as_of={as_of} | limit={args.limit}")

    if args.mode in ("scores", "both"):
        df = compute_daily_scores(config, as_of, limit=args.limit)
        if not df.empty:
            save_daily_scores(df, config, as_of)
            picks = recommend_top3(df, config)
            if picks:
                msg = notifier.format_top3(picks)
                notifier.send_message(msg)
                logger.info(f"top3: {[(p['ticker'], p['total_score']) for p in picks]}")
            else:
                logger.info("no picks passed minimum score threshold")

    if args.mode in ("signals", "both"):
        alerts = check_sell_signals(config, as_of)
        for a in alerts:
            msg = notifier.format_sell_alert(a["decision"], a["position"], a["current_price"])
            notifier.send_message(msg)
            logger.info(f"sell alert: {a['ticker']} {a['decision']['reason']}")

    logger.info("run_daily done.")


if __name__ == "__main__":
    main()
