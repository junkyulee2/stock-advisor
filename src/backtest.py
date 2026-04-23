"""Walk-forward backtest engine.

Simulates the daily pipeline across history: compute scores for day T using
data up to T-1, buy at T's open, manage positions, exit via same rules.
Evaluates vs KOSPI benchmark.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from .utils import setup_logger

logger = setup_logger(__name__)


@dataclass
class BacktestConfig:
    start_date: str
    end_date: str
    initial_capital: float = 1_000_000
    transaction_cost_pct: float = 0.3   # round trip
    slippage_pct: float = 0.1
    max_positions: int = 5
    rebalance_every_days: int = 1       # 1 = daily signals


@dataclass
class BacktestResult:
    equity_curve: pd.Series = field(default_factory=pd.Series)
    benchmark_curve: pd.Series = field(default_factory=pd.Series)
    trades: list = field(default_factory=list)
    daily_returns: pd.Series = field(default_factory=pd.Series)

    @property
    def total_return(self) -> float:
        if self.equity_curve.empty:
            return 0
        return float(self.equity_curve.iloc[-1] / self.equity_curve.iloc[0] - 1)

    @property
    def benchmark_return(self) -> float:
        if self.benchmark_curve.empty:
            return 0
        return float(self.benchmark_curve.iloc[-1] / self.benchmark_curve.iloc[0] - 1)

    @property
    def alpha(self) -> float:
        return self.total_return - self.benchmark_return

    @property
    def max_drawdown(self) -> float:
        if self.equity_curve.empty:
            return 0
        peak = self.equity_curve.cummax()
        dd = (self.equity_curve - peak) / peak
        return float(dd.min())

    @property
    def sharpe_ratio(self) -> float:
        if self.daily_returns.empty or self.daily_returns.std() == 0:
            return 0
        annualization = np.sqrt(252)
        return float(self.daily_returns.mean() / self.daily_returns.std() * annualization)

    @property
    def win_rate(self) -> float:
        closed = [t for t in self.trades if t.get("pnl") is not None]
        if not closed:
            return 0
        wins = [t for t in closed if t["pnl"] > 0]
        return len(wins) / len(closed)

    def summary(self) -> dict:
        closed = [t for t in self.trades if t.get("pnl") is not None]
        return {
            "total_return_pct": self.total_return * 100,
            "benchmark_return_pct": self.benchmark_return * 100,
            "alpha_pct": self.alpha * 100,
            "max_drawdown_pct": self.max_drawdown * 100,
            "sharpe_ratio": self.sharpe_ratio,
            "win_rate": self.win_rate,
            "total_trades": len(closed),
            "start_capital": float(self.equity_curve.iloc[0]) if not self.equity_curve.empty else 0,
            "end_capital": float(self.equity_curve.iloc[-1]) if not self.equity_curve.empty else 0,
        }


def passes_criteria(result: BacktestResult, criteria: dict) -> tuple[bool, list[str]]:
    """Check if result passes config-defined criteria."""
    failures = []
    if result.alpha * 100 < criteria.get("min_alpha_vs_benchmark", 0):
        failures.append(
            f"alpha {result.alpha*100:.2f}% < {criteria['min_alpha_vs_benchmark']}%"
        )
    if abs(result.max_drawdown) * 100 > criteria.get("max_drawdown_pct", 100):
        failures.append(
            f"MDD {result.max_drawdown*100:.2f}% > {criteria['max_drawdown_pct']}%"
        )
    if result.sharpe_ratio < criteria.get("min_sharpe_ratio", 0):
        failures.append(
            f"Sharpe {result.sharpe_ratio:.2f} < {criteria['min_sharpe_ratio']}"
        )
    if result.win_rate < criteria.get("min_win_rate", 0):
        failures.append(
            f"win_rate {result.win_rate:.2%} < {criteria['min_win_rate']:.2%}"
        )
    return (len(failures) == 0, failures)


# NOTE: full backtest implementation is heavy (5y * 500 tickers fetch).
# Placeholder skeleton here; full implementation will iterate once initial
# paper trading validates the engine end-to-end. See README.md Phase 2.

def run_backtest(config: dict, bt_config: BacktestConfig) -> BacktestResult:
    """Placeholder. Will be implemented after MVP is end-to-end verified."""
    logger.warning(
        "run_backtest() is a stub. Implement after MVP works. "
        "Phase 2: use historical pykrx data to replay the daily pipeline."
    )
    return BacktestResult()
