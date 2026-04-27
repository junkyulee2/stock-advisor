"""One-time migration: backfill entry_factors for legacy positions.

Existing positions bought before 2026-04-27 don't have `entry_factors`
recorded. This script reads each position's `entry_date`, opens the
corresponding `data/scores/scores_YYYYMMDD.json`, and copies the 5-factor
breakdown into the position. Skips positions that already have factors.

Usage:
    python tools/backfill_entry_factors.py [--dry-run]

Run once locally; commit the updated portfolio.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows so Korean names/punctuation don't crash CP949.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


FACTOR_KEYS = [
    ("momentum",       "momentum_score"),
    ("supply_demand",  "supply_demand_score"),
    ("quality",        "quality_score"),
    ("volatility",     "volatility_score"),
    ("mean_reversion", "mean_reversion_score"),
]


def _load_scores_file(date_str: str) -> dict:
    """date_str is 'YYYY-MM-DD' or 'YYYYMMDD'. Returns {ticker: row} or {}."""
    compact = date_str.replace("-", "")
    path = PROJECT_ROOT / "data" / "scores" / f"scores_{compact}.json"
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ! failed to read {path.name}: {e}")
        return {}
    return {r["ticker"]: r for r in rows}


def _find_factor_row(ticker: str, entry_date: str,
                     scores_cache: dict[str, dict]) -> tuple[dict | None, str]:
    """Look for ticker in entry_date's scores. If not found, walk back up to
    7 days searching the closest prior file. Returns (row, source_date)."""
    from datetime import datetime, timedelta
    try:
        d = datetime.strptime(entry_date, "%Y-%m-%d")
    except Exception:
        return None, ""
    for offset in range(0, 8):
        probe = (d - timedelta(days=offset)).strftime("%Y-%m-%d")
        if probe not in scores_cache:
            scores_cache[probe] = _load_scores_file(probe)
        row = scores_cache[probe].get(ticker)
        if row:
            return row, probe
    return None, ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing.")
    args = parser.parse_args()

    portfolio_path = PROJECT_ROOT / "data" / "portfolio.json"
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    positions = portfolio.get("positions", {})

    if not positions:
        print("no positions; nothing to do.")
        return

    print(f"scanning {len(positions)} positions...")
    scores_cache: dict[str, dict] = {}
    backfilled = 0
    skipped = 0
    missing = 0

    for ticker, pos in positions.items():
        name = pos.get("name", "")
        if pos.get("entry_factors"):
            print(f"  [skip] {ticker} {name} - already has entry_factors")
            skipped += 1
            continue
        entry_date = pos.get("entry_date", "")
        if not entry_date:
            print(f"  [skip] {ticker} {name} - no entry_date")
            skipped += 1
            continue

        row, source_date = _find_factor_row(ticker, entry_date, scores_cache)
        if not row:
            print(f"  [miss] {ticker} {name} - no score row near {entry_date} (7d back)")
            missing += 1
            continue

        factors = {k: float(row.get(score_key, 0) or 0)
                   for k, score_key in FACTOR_KEYS}
        pos["entry_factors"] = factors
        cur_total = float(row.get("total_score", 0) or 0)
        # Also overwrite entry_score with the same scoring snapshot so
        # downstream degradation comparison stays self-consistent
        # (avoids stale pre-calibration score causing false sell signals).
        old_score = pos.get("entry_score", 0)
        pos["entry_score"] = cur_total
        from_str = "" if source_date == entry_date else f" (from {source_date})"
        print(f"  [ok]   {ticker} {name} entry={entry_date}{from_str} "
              f"score {old_score:.1f}->{cur_total:.1f} "
              f"M={factors['momentum']:.0f} "
              f"S={factors['supply_demand']:.0f} "
              f"Q={factors['quality']:.0f} "
              f"V={factors['volatility']:.0f} "
              f"R={factors['mean_reversion']:.0f}")
        backfilled += 1

    print()
    print(f"summary: {backfilled} backfilled, {skipped} skipped, {missing} missing")
    if backfilled == 0:
        print("nothing to write.")
        return
    if args.dry_run:
        print("DRY RUN - portfolio.json NOT written.")
        return
    portfolio_path.write_text(
        json.dumps(portfolio, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {portfolio_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
