"""Simple disk cache for data fetches.

Keys are explicit strings (func + args). TTL varies by data type.
Storage: pickle files under data/cache/.
"""
from __future__ import annotations

import hashlib
import pickle
import time
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from .utils import PROJECT_ROOT, setup_logger

logger = setup_logger(__name__)

CACHE_DIR = PROJECT_ROOT / "data" / "cache"


def _safe_key(raw: str) -> str:
    """Turn an arbitrary key into a safe filename. Short prefix + hash."""
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
    prefix = "".join(c if c.isalnum() or c in "._-" else "_" for c in raw[:40])
    return f"{prefix}_{digest}"


def cache_get(key: str, ttl_seconds: int) -> Optional[Any]:
    path = CACHE_DIR / f"{_safe_key(key)}.pkl"
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ttl_seconds:
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        logger.debug(f"cache read fail {key}: {e}")
        return None


def cache_set(key: str, value: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_safe_key(key)}.pkl"
    try:
        with open(path, "wb") as f:
            pickle.dump(value, f)
    except Exception as e:
        logger.debug(f"cache write fail {key}: {e}")


def _is_empty_result(val: Any) -> bool:
    if val is None:
        return True
    if hasattr(val, "empty"):
        try:
            return bool(val.empty)
        except Exception:
            return False
    if isinstance(val, (list, dict, tuple)) and len(val) == 0:
        return True
    return False


def disk_cached(key_fn: Callable[..., str], ttl: int):
    """Decorator: cache function result on disk with TTL seconds.

    Empty DataFrames / None / empty collections are NOT cached so transient
    fetch failures don't poison the cache.
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs)
            hit = cache_get(key, ttl)
            if hit is not None and not _is_empty_result(hit):
                return hit
            result = func(*args, **kwargs)
            if not _is_empty_result(result):
                cache_set(key, result)
            return result
        return wrapper
    return decorator


def clear_cache() -> int:
    """Remove all cache files. Returns file count deleted."""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for p in CACHE_DIR.glob("*.pkl"):
        try:
            p.unlink()
            n += 1
        except Exception:
            pass
    return n


def cache_stats() -> dict:
    if not CACHE_DIR.exists():
        return {"files": 0, "size_mb": 0}
    files = list(CACHE_DIR.glob("*.pkl"))
    size = sum(p.stat().st_size for p in files)
    return {"files": len(files), "size_mb": size / 1024 / 1024}
