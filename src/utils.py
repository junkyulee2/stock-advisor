"""Common utilities: config loading, logging, date helpers."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_config(path: Path | str = CONFIG_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logger(name: str = "stock", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.addHandler(handler)
    return logger


def load_json(path: Path | str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default if default is not None else {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path | str, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def previous_trading_day(d: datetime | None = None) -> str:
    """Return YYYYMMDD for the previous weekday (rough proxy for trading day).
    pykrx handles actual holiday skipping when querying."""
    if d is None:
        d = datetime.now()
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d.strftime("%Y%m%d")


def today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def iso_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")
