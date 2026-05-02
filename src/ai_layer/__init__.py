"""AI Layer (Phase C, 2026-05 redesign).

Qualitative filter using Claude Code Max 5x as a veto mechanism over
rule-based scoring. AI cannot recommend buys — only block them via
PASS / CAUTION / REJECT verdicts.

See memory/project_stock_redesign_2026-05.md for full spec.
"""
from .budget import (
    BudgetExceeded,
    check_budget,
    record_usage,
    load_usage,
    usage_summary,
)

__all__ = [
    "BudgetExceeded",
    "check_budget",
    "record_usage",
    "load_usage",
    "usage_summary",
]
