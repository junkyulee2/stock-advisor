"""View-data builders. Pure functions: input data + portfolio → template context."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .data_layer import (
    CONFIG, CLOUD_MODE,
    load_portfolio, load_history, latest_scores, previous_scores,
)
from .price_fetcher import fetch_current_prices, fetch_price_history

MIN_SCORE_TO_BUY = int(CONFIG["portfolio_limits"]["min_score_to_buy"])
MAX_POSITIONS = int(CONFIG["portfolio_limits"].get("max_concurrent_positions", 10))


# ---------- helpers ----------

def _filename_to_date(name: str | None) -> str:
    """'scores_20260424.json' -> '2026-04-24'."""
    if not name:
        return "-"
    s = name.replace("scores_", "").replace(".json", "")
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _rank_map(scores: list[dict]) -> dict[str, int]:
    return {s["ticker"]: i + 1 for i, s in enumerate(scores)}


def _rank_delta(ticker: str, today_rank: int, prev_map: dict) -> dict:
    prev = prev_map.get(ticker)
    if prev is None:
        return {"label": "🆕 NEW", "kind": "new"}
    delta = prev - today_rank
    if delta > 0:
        return {"label": f"↑ {delta}", "kind": "up"}
    if delta < 0:
        return {"label": f"↓ {-delta}", "kind": "down"}
    return {"label": "→", "kind": "flat"}


def _amount_for_score(score: float) -> int:
    """Sizing ladder: 95+ → 300k, 90+ → 200k, 85+ → 100k, 80+ → 50k."""
    rules = CONFIG.get("investment_rules", [])
    for r in sorted(rules, key=lambda x: -x["min_score"]):
        if score >= r["min_score"]:
            return int(r["amount_krw"])
    return 0


def _enrich_score_row(row: dict, prev_map: dict, today_rank: int) -> dict:
    """Add derived fields used by templates: rank, badge, sizing."""
    score = float(row.get("total_score", 0))
    out = dict(row)
    out["rank"] = today_rank
    out["delta"] = _rank_delta(row["ticker"], today_rank, prev_map)
    if not row.get("amount_krw"):
        out["amount_krw"] = _amount_for_score(score)
    return out


# ---------- main builders ----------

def build_dashboard_context() -> dict:
    """All data needed for the dashboard page."""
    scores, scores_file = latest_scores()
    prev_scores_data, _ = previous_scores()
    portfolio, _ = load_portfolio()
    history, _ = load_history()
    positions = portfolio.get("positions", {})

    # Sort scores desc
    scores = sorted(scores, key=lambda x: -float(x.get("total_score", 0)))

    # Maps
    prev_map = _rank_map(prev_scores_data)
    held_tickers = set(positions.keys())

    # Recommendations: 80+ excluding held
    recs_raw = [s for s in scores
                if float(s.get("total_score", 0)) >= MIN_SCORE_TO_BUY
                and s["ticker"] not in held_tickers]
    recommendations = [
        _enrich_score_row(s, prev_map, today_rank=i + 1)
        for i, s in enumerate(recs_raw[:5])
    ]

    # Reference picks (sub-threshold, top 2 excluding held)
    references: list[dict] = []
    if not recommendations:
        ref_raw = [s for s in scores if s["ticker"] not in held_tickers][:2]
        references = [
            _enrich_score_row(s, prev_map, today_rank=i + 1)
            for i, s in enumerate(ref_raw)
        ]

    # Top score regardless of holdings (for empty-state hint)
    top_score = float(scores[0]["total_score"]) if scores else 0.0

    # Current prices for held + top picks
    prices_needed = tuple(set(list(held_tickers) + [s["ticker"] for s in scores[:10]]))
    current_prices = fetch_current_prices(prices_needed)

    # Holdings list with P/L
    holdings = _build_holdings(positions, current_prices)

    # KPIs
    kpis = _compute_kpis(portfolio, history, holdings)

    # Portfolio value series (last 30 days, for chart)
    chart = _build_chart_data(positions)

    return {
        "scores_date": _filename_to_date(scores_file),
        "today_str": datetime.now().strftime("%Y-%m-%d (%a)"),
        "min_score": MIN_SCORE_TO_BUY,
        "max_positions": MAX_POSITIONS,
        "top_score": top_score,
        "scores_count": len(scores),

        "recommendations": recommendations,
        "references": references,
        "holdings": holdings,
        "kpis": kpis,
        "chart": chart,

        "cloud_mode": CLOUD_MODE,
    }


def build_recommendations_context() -> dict:
    """All buyable picks (80+) and reference picks."""
    ctx = build_dashboard_context()
    return ctx


def build_holdings_context() -> dict:
    portfolio, _ = load_portfolio()
    positions = portfolio.get("positions", {})
    held_tickers = tuple(positions.keys())
    current_prices = fetch_current_prices(held_tickers)
    holdings = _build_holdings(positions, current_prices)
    history, _ = load_history()
    kpis = _compute_kpis(portfolio, history, holdings)
    return {
        "holdings": holdings,
        "kpis": kpis,
        "today_str": datetime.now().strftime("%Y-%m-%d (%a)"),
        "min_score": MIN_SCORE_TO_BUY,
        "max_positions": MAX_POSITIONS,
        "cloud_mode": CLOUD_MODE,
    }


def build_history_context(limit: int = 200) -> dict:
    history, _ = load_history()
    trades = history.get("trades", [])
    trades_sorted = sorted(
        trades, key=lambda t: (t.get("exit_date") or t.get("entry_date") or ""),
        reverse=True,
    )[:limit]
    portfolio, _ = load_portfolio()
    held_tickers = tuple(portfolio.get("positions", {}).keys())
    prices = fetch_current_prices(held_tickers) if held_tickers else {}
    holdings = _build_holdings(portfolio.get("positions", {}), prices)
    kpis = _compute_kpis(portfolio, history, holdings)
    return {
        "trades": trades_sorted,
        "trade_count": len(trades),
        "kpis": kpis,
        "today_str": datetime.now().strftime("%Y-%m-%d (%a)"),
        "min_score": MIN_SCORE_TO_BUY,
        "max_positions": MAX_POSITIONS,
        "cloud_mode": CLOUD_MODE,
    }


def build_analytics_context() -> dict:
    portfolio, _ = load_portfolio()
    history, _ = load_history()
    held_tickers = tuple(portfolio.get("positions", {}).keys())
    prices = fetch_current_prices(held_tickers) if held_tickers else {}
    holdings = _build_holdings(portfolio.get("positions", {}), prices)
    kpis = _compute_kpis(portfolio, history, holdings)

    # Score distribution from latest
    scores, _ = latest_scores()
    bins = {"95+": 0, "90-94": 0, "85-89": 0, "80-84": 0, "70-79": 0, "<70": 0}
    for s in scores:
        v = float(s.get("total_score", 0))
        if v >= 95: bins["95+"] += 1
        elif v >= 90: bins["90-94"] += 1
        elif v >= 85: bins["85-89"] += 1
        elif v >= 80: bins["80-84"] += 1
        elif v >= 70: bins["70-79"] += 1
        else: bins["<70"] += 1

    return {
        "kpis": kpis,
        "score_bins": bins,
        "today_str": datetime.now().strftime("%Y-%m-%d (%a)"),
        "min_score": MIN_SCORE_TO_BUY,
        "max_positions": MAX_POSITIONS,
        "cloud_mode": CLOUD_MODE,
    }


# ---------- shared computations ----------

def _build_holdings(positions: dict, current_prices: dict) -> list[dict]:
    out: list[dict] = []
    for ticker, p in positions.items():
        entry = float(p.get("entry_price", 0) or 0)
        qty = int(p.get("qty", 0) or 0)
        cur = float(current_prices.get(ticker, entry) or entry)
        market_value = cur * qty
        pnl = (cur - entry) * qty
        pnl_pct = ((cur - entry) / entry * 100) if entry > 0 else 0
        out.append({
            "ticker": ticker,
            "name": p.get("name", ticker),
            "entry_date": p.get("entry_date", "-"),
            "entry_price": entry,
            "current_price": cur,
            "qty": qty,
            "cost_krw": int(p.get("cost_krw", 0) or 0),
            "market_value": int(market_value),
            "pnl_krw": int(pnl),
            "pnl_pct": pnl_pct,
            "entry_score": float(p.get("entry_score", 0) or 0),
            "sector": p.get("sector", "-"),
            "highest_price": float(p.get("highest_price", 0) or 0),
            "partial_taken": bool(p.get("partial_taken", False)),
        })
    out.sort(key=lambda x: -x["pnl_pct"])
    return out


def _compute_kpis(portfolio: dict, history: dict, holdings: list[dict]) -> dict:
    trades = history.get("trades", [])
    sells = [t for t in trades if t.get("action") == "sell"]
    realized = sum(float(t.get("pnl_krw", 0) or 0) for t in sells)
    wins = [t for t in sells if float(t.get("pnl_krw", 0) or 0) > 0]
    losses = [t for t in sells if float(t.get("pnl_krw", 0) or 0) <= 0]
    win_rate = (len(wins) / len(sells) * 100) if sells else 0

    total_market_value = sum(h["market_value"] for h in holdings)
    total_cost = sum(h["cost_krw"] for h in holdings)
    unrealized = total_market_value - total_cost
    today_change = sum(h["pnl_krw"] for h in holdings)  # rough proxy

    cash = int(portfolio.get("cash_krw", 0) or 0)
    total_value = total_market_value + cash + int(realized)

    return {
        "total_value": int(total_value),
        "market_value": int(total_market_value),
        "cash": cash,
        "realized": int(realized),
        "unrealized": int(unrealized),
        "today_change": int(today_change),
        "today_change_pct": ((today_change / total_cost * 100) if total_cost else 0),
        "open_positions": len(holdings),
        "max_positions": MAX_POSITIONS,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "trade_count": len(trades),
        "sell_count": len(sells),
    }


def _build_chart_data(positions: dict) -> dict:
    """Build SVG-friendly point list for portfolio value over last ~30 trading days."""
    if not positions:
        return {"points": [], "labels": [], "min": 0, "max": 0, "current": 0}

    tickers = tuple(positions.keys())
    df = fetch_price_history(tickers, days=30)
    if df.empty:
        return {"points": [], "labels": [], "min": 0, "max": 0, "current": 0}

    # Per-day total cost-basis P/L
    series_vals: list[float] = []
    labels: list[str] = []
    base_cost = sum(float(p.get("cost_krw", 0) or 0) for p in positions.values())
    for idx, row in df.iterrows():
        mv = 0.0
        for tk, close in row.items():
            if pd.isna(close) or tk not in positions:
                continue
            mv += float(close) * int(positions[tk].get("qty", 0) or 0)
        series_vals.append(mv)
        labels.append(idx.strftime("%m/%d"))

    if not series_vals:
        return {"points": [], "labels": [], "min": 0, "max": 0, "current": 0}

    lo, hi = min(series_vals), max(series_vals)
    if hi == lo:
        hi = lo + 1  # avoid div-by-zero

    # Normalize to SVG coords: x ∈ [0,700], y ∈ [10,250] (inverted)
    n = len(series_vals)
    points = []
    for i, v in enumerate(series_vals):
        x = (i / max(n - 1, 1)) * 700
        y = 250 - ((v - lo) / (hi - lo)) * 240
        points.append((round(x, 1), round(y, 1)))

    return {
        "points": points,
        "labels": labels,
        "min": int(lo),
        "max": int(hi),
        "current": int(series_vals[-1]),
        "base_cost": int(base_cost),
    }
