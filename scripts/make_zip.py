"""Bundle the project into a zip for sharing / running on another PC.

Excludes: venv, __pycache__, .git, data/scores/*, secrets, credentials, *.pyc.
"""
from __future__ import annotations

import shutil
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DESKTOP_CANDIDATES = [
    Path.home() / "OneDrive" / "바탕 화면",
    Path.home() / "OneDrive" / "Desktop",
    Path.home() / "Desktop",
    Path.home() / "바탕 화면",
]

EXCLUDE_DIRS = {"venv", "__pycache__", ".git", "node_modules", ".pytest_cache", ".mypy_cache"}
EXCLUDE_FILES = {"secrets.toml", "credentials.toml", ".DS_Store"}
EXCLUDE_EXTS = {".pyc", ".pyo", ".log"}
EXCLUDE_PATHS = {
    "data/scores",
    "data/cache",
    "data/backtest",
}


def find_desktop() -> Path:
    for p in DESKTOP_CANDIDATES:
        if p.exists():
            return p
    return Path.home()


def should_skip(path: Path, rel: Path) -> bool:
    parts = rel.parts
    for d in EXCLUDE_DIRS:
        if d in parts:
            return True
    if path.name in EXCLUDE_FILES:
        return True
    if path.suffix.lower() in EXCLUDE_EXTS:
        return True
    rel_str = rel.as_posix()
    for ep in EXCLUDE_PATHS:
        if rel_str.startswith(ep):
            return True
    return False


def main() -> None:
    desktop = find_desktop()
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    zip_path = desktop / f"Stock Advisor - project ({stamp}).zip"

    count = 0
    size_total = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=7) as zf:
        for p in PROJECT_ROOT.rglob("*"):
            if p.is_dir():
                continue
            rel = p.relative_to(PROJECT_ROOT)
            if should_skip(p, rel):
                continue
            zf.write(p, arcname=str(rel))
            count += 1
            size_total += p.stat().st_size

    kb = zip_path.stat().st_size / 1024
    print(f"Zip saved: {zip_path}")
    print(f"Files: {count} | Source bytes: {size_total:,} | Zip size: {kb:,.1f} KB")


if __name__ == "__main__":
    main()
