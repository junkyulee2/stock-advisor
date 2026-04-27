"""Portfolio management — paper trading positions and transaction history."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from .utils import load_json, save_json, iso_today


def empty_portfolio() -> dict:
    return {
        "cash_krw": 0,
        "positions": {},  # ticker -> position dict
        "updated_at": iso_today(),
    }


def empty_history() -> dict:
    return {"trades": []}


def load_portfolio(path: Path | str) -> dict:
    return load_json(path, default=empty_portfolio())


def load_history(path: Path | str) -> dict:
    return load_json(path, default=empty_history())


def buy(
    portfolio: dict,
    ticker: str,
    name: str,
    price: float,
    amount_krw: int,
    score: float,
    date: Optional[str] = None,
    sector: Optional[str] = None,
    min_qty: int = 1,
    factors: Optional[dict] = None,
) -> dict:
    """Execute a paper buy. Returns the new position.

    Minimum 1 share is always purchased — Korean market has no fractional
    shares, and skipping expensive picks from Top 3 would distort results.
    `actual_cost` may exceed `amount_krw` if 1 share > budget (e.g., 황제주).

    `factors`: 5-factor breakdown at entry, used later for "factor degradation"
    sell signals. Keys: momentum, supply_demand, quality, volatility, mean_reversion.
    """
    if price <= 0:
        raise ValueError(f"invalid price: {price}")
    target_qty = int(amount_krw // price)
    qty = max(min_qty, target_qty)
    actual_cost = qty * price
    d = date or iso_today()

    pos = {
        "ticker": ticker,
        "name": name,
        "entry_price": float(price),
        "entry_date": d,
        "qty": qty,
        "initial_qty": qty,
        "cost_krw": actual_cost,
        "entry_score": float(score),
        "entry_factors": factors or {},
        "sector": sector,
        "highest_price": float(price),
        "partial_taken": False,
        "realized_pnl_krw": 0,
    }
    portfolio["positions"][ticker] = pos
    portfolio["updated_at"] = iso_today()
    return pos


def update_highest(portfolio: dict, ticker: str, current_price: float) -> None:
    p = portfolio["positions"].get(ticker)
    if p and current_price > p.get("highest_price", 0):
        p["highest_price"] = float(current_price)


def add_to_position(
    portfolio: dict,
    ticker: str,
    price: float,
    amount_krw: int,
    score: float,
    date: Optional[str] = None,
    min_qty: int = 1,
) -> tuple[dict, int]:
    """Pyramid-add to an existing position. Recomputes weighted-avg entry.

    Returns (updated position, new shares purchased).
    """
    pos = portfolio["positions"].get(ticker)
    if not pos:
        raise ValueError(f"no existing position: {ticker}")
    if price <= 0:
        raise ValueError(f"invalid price: {price}")

    target_qty = int(amount_krw // price)
    qty = max(min_qty, target_qty)
    new_cost = qty * price

    pos["qty"] += qty
    pos["initial_qty"] = pos.get("initial_qty", pos["qty"] - qty) + qty
    pos["cost_krw"] = pos.get("cost_krw", 0) + new_cost
    # Weighted-average entry price across all buys
    pos["entry_price"] = pos["cost_krw"] / pos["initial_qty"]
    if price > pos.get("highest_price", 0):
        pos["highest_price"] = float(price)
    pos["last_added_date"] = date or iso_today()
    pos["add_count"] = pos.get("add_count", 0) + 1
    portfolio["updated_at"] = iso_today()
    return pos, qty


def record_add_history(
    history: dict,
    ticker: str,
    name: str,
    qty: int,
    price: float,
    score: float,
    date: Optional[str] = None,
) -> None:
    """Record a pyramid-add as a separate history entry (action=buy, type=add)."""
    history["trades"].append({
        "ticker": ticker,
        "name": name,
        "action": "buy",
        "type": "add",
        "qty": qty,
        "price": float(price),
        "cost_krw": qty * price,
        "entry_score": float(score),
        "entry_date": date or iso_today(),
    })


def sell(
    portfolio: dict,
    history: dict,
    ticker: str,
    price: float,
    sell_ratio: float,
    reason: str,
    date: Optional[str] = None,
) -> dict:
    """Sell a fraction (or all) of a position. Appends to history."""
    pos = portfolio["positions"].get(ticker)
    if not pos:
        raise ValueError(f"no position for {ticker}")

    sell_qty = int(pos["qty"] * sell_ratio) if sell_ratio < 1.0 else pos["qty"]
    if sell_qty <= 0:
        sell_qty = pos["qty"]

    proceeds = sell_qty * price
    avg_cost_per_share = pos["cost_krw"] / pos["initial_qty"]
    cost_of_sold = avg_cost_per_share * sell_qty
    pnl = proceeds - cost_of_sold
    pnl_pct = (price / pos["entry_price"] - 1) * 100

    d = date or iso_today()
    trade = {
        "ticker": ticker,
        "name": pos["name"],
        "action": "sell",
        "sell_ratio": sell_ratio,
        "qty": sell_qty,
        "price": float(price),
        "proceeds_krw": proceeds,
        "pnl_krw": pnl,
        "pnl_pct": pnl_pct,
        "reason": reason,
        "entry_price": pos["entry_price"],
        "entry_date": pos["entry_date"],
        "exit_date": d,
    }
    history["trades"].append(trade)

    pos["qty"] -= sell_qty
    pos["realized_pnl_krw"] = pos.get("realized_pnl_krw", 0) + pnl

    if pos["qty"] <= 0:
        # Fully closed
        del portfolio["positions"][ticker]
    else:
        pos["partial_taken"] = True

    portfolio["updated_at"] = iso_today()
    return trade


def record_buy_history(history: dict, position: dict) -> None:
    history["trades"].append({
        "ticker": position["ticker"],
        "name": position["name"],
        "action": "buy",
        "qty": position["qty"],
        "price": position["entry_price"],
        "cost_krw": position["cost_krw"],
        "entry_score": position["entry_score"],
        "entry_date": position["entry_date"],
    })


def sector_count(portfolio: dict, sector: Optional[str]) -> int:
    if not sector:
        return 0
    return sum(1 for p in portfolio["positions"].values() if p.get("sector") == sector)


def position_count(portfolio: dict) -> int:
    return len(portfolio["positions"])


def compute_summary(portfolio: dict, history: dict, current_prices: dict[str, float]) -> dict:
    """Summarize overall performance.

    current_prices: {ticker: close_price}
    """
    positions = portfolio["positions"]
    unrealized = 0.0
    total_cost_open = 0.0
    for t, p in positions.items():
        cp = current_prices.get(t, p["entry_price"])
        unrealized += (cp - p["entry_price"]) * p["qty"]
        total_cost_open += p["entry_price"] * p["qty"]

    realized = sum(
        tr.get("pnl_krw", 0) for tr in history["trades"] if tr["action"] == "sell"
    )

    wins = [tr for tr in history["trades"] if tr["action"] == "sell" and tr.get("pnl_krw", 0) > 0]
    losses = [tr for tr in history["trades"] if tr["action"] == "sell" and tr.get("pnl_krw", 0) <= 0]
    closed = wins + losses
    win_rate = (len(wins) / len(closed)) if closed else 0.0

    return {
        "open_positions": len(positions),
        "unrealized_pnl_krw": unrealized,
        "realized_pnl_krw": realized,
        "total_pnl_krw": unrealized + realized,
        "open_cost_krw": total_cost_open,
        "trades_count": len(history["trades"]),
        "closed_trades": len(closed),
        "win_rate": win_rate,
        "wins": len(wins),
        "losses": len(losses),
    }
