"""Cloud-aware data I/O wrapper around src/cloud_store.py and local files.

Mirrors the behavior of app.py's _cloud_read / _cloud_write but without
Streamlit-specific caching. Uses a tiny TTL cache for read paths.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src import cloud_store, portfolio as pf
from src.utils import load_config, save_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config()
CLOUD_MODE = cloud_store.is_configured()

# Resolved paths (relative-to-repo strings; used by cloud_store too)
PORTFOLIO_PATH = CONFIG["paths"]["portfolio"]      # "data/portfolio.json"
HISTORY_PATH = CONFIG["paths"]["history"]          # "data/history.json"
SCORES_DIR = CONFIG["paths"]["scores_dir"]         # "data/scores"

_READ_TTL_SECONDS = 20
_read_cache: dict[str, tuple[float, Any, str | None]] = {}


def _now() -> float:
    return time.time()


def cloud_read(path: str, *, force: bool = False) -> tuple[Any, str | None]:
    """Returns (data, sha). sha is None for local mode. Cached 20s by path."""
    cached = _read_cache.get(path)
    if not force and cached and (_now() - cached[0]) < _READ_TTL_SECONDS:
        return cached[1], cached[2]

    if CLOUD_MODE:
        try:
            data, sha = cloud_store.read_json(path)
        except Exception:
            data, sha = None, None
    else:
        local = PROJECT_ROOT / path
        if local.exists():
            with open(local, "r", encoding="utf-8") as f:
                data = json.load(f)
            sha = None
        else:
            data, sha = None, None

    _read_cache[path] = (_now(), data, sha)
    return data, sha


def cloud_write(path: str, data: dict, sha: str | None, message: str) -> bool:
    if CLOUD_MODE:
        try:
            cloud_store.write_json(path, data, sha, message)
            _read_cache.pop(path, None)
            return True
        except Exception:
            return False
    local = PROJECT_ROOT / path
    local.parent.mkdir(parents=True, exist_ok=True)
    save_json(local, data)
    _read_cache.pop(path, None)
    return True


def invalidate_cache(path: str | None = None) -> None:
    if path is None:
        _read_cache.clear()
    else:
        _read_cache.pop(path, None)


def load_portfolio() -> tuple[dict, str | None]:
    data, sha = cloud_read(PORTFOLIO_PATH)
    if data is None:
        data = pf.empty_portfolio()
    return data, sha


def load_history() -> tuple[dict, str | None]:
    data, sha = cloud_read(HISTORY_PATH)
    if data is None:
        data = pf.empty_history()
    return data, sha


def _list_score_filenames() -> list[str]:
    """Return ['scores_YYYYMMDD.json', ...] sorted newest-first.

    Local mode: glob the directory.
    Cloud mode: probe last 14 days (no list API in cloud_store).
    """
    if CLOUD_MODE:
        from datetime import datetime, timedelta
        today = datetime.now()
        names: list[str] = []
        for i in range(14):
            d = today - timedelta(days=i)
            names.append(f"scores_{d.strftime('%Y%m%d')}.json")
        return names
    local_dir = PROJECT_ROOT / SCORES_DIR
    if not local_dir.exists():
        return []
    return [p.name for p in sorted(local_dir.glob("scores_*.json"), reverse=True)]


def latest_scores() -> tuple[list[dict], str | None]:
    """Returns (scores list, source filename) for the most recent scores file.

    Scores are stored as `data/scores/scores_YYYYMMDD.json`.
    """
    for name in _list_score_filenames():
        data, _ = cloud_read(f"{SCORES_DIR}/{name}")
        if data:
            return data, name
    return [], None


def previous_scores() -> tuple[list[dict], str | None]:
    """Second-most-recent scores file (for day-over-day rank delta)."""
    found = 0
    for name in _list_score_filenames():
        data, _ = cloud_read(f"{SCORES_DIR}/{name}")
        if data:
            found += 1
            if found == 2:
                return data, name
    return [], None
