"""Cloud store — read/write JSON files in the GitHub repo via API.

Used by the Streamlit web app so buy/sell actions persist back to the repo,
which both web and GitHub Actions pipelines read.

Auth: `GITHUB_TOKEN` and `GITHUB_REPO` (e.g. "junkyulee2/stock-advisor") are
expected in env vars or Streamlit secrets.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional


def _get_token() -> Optional[str]:
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    try:
        import streamlit as st
        return st.secrets.get("GITHUB_TOKEN")
    except Exception:
        return None


def _get_repo_name() -> str:
    name = os.environ.get("GITHUB_REPO")
    if name:
        return name
    try:
        import streamlit as st
        n = st.secrets.get("GITHUB_REPO")
        if n:
            return n
    except Exception:
        pass
    return "junkyulee2/stock-advisor"


def is_configured() -> bool:
    return _get_token() is not None


def _repo():
    from github import Github
    tok = _get_token()
    if not tok:
        raise RuntimeError("GITHUB_TOKEN not configured in env or Streamlit secrets")
    return Github(tok).get_repo(_get_repo_name())


def read_json(path: str) -> tuple[Any, Optional[str]]:
    """Return (parsed_json, sha). sha is the file's git SHA needed for updates."""
    repo = _repo()
    content = repo.get_contents(path, ref="main")
    data = json.loads(content.decoded_content.decode("utf-8"))
    return data, content.sha


def write_json(path: str, data: Any, sha: Optional[str], message: str) -> str:
    """Write/update a JSON file. Returns new SHA."""
    repo = _repo()
    body = json.dumps(data, ensure_ascii=False, indent=2)
    if sha:
        result = repo.update_file(path=path, message=message, content=body,
                                  sha=sha, branch="main")
    else:
        result = repo.create_file(path=path, message=message, content=body,
                                  branch="main")
    return result["content"].sha


def trigger_workflow(workflow_file: str = "daily_score.yml", ref: str = "main") -> None:
    """Dispatch a GitHub Actions workflow run (manual trigger).

    Requires GITHUB_TOKEN with `actions: write` scope (classic `repo` scope
    covers it). Raises on failure.
    """
    import requests
    token = _get_token()
    if not token:
        raise RuntimeError("GITHUB_TOKEN not configured")
    repo_name = _get_repo_name()
    url = (f"https://api.github.com/repos/{repo_name}"
           f"/actions/workflows/{workflow_file}/dispatches")
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": ref},
        timeout=15,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"workflow dispatch failed ({r.status_code}): {r.text[:200]}")


def last_workflow_run(workflow_file: str = "daily_score.yml") -> Optional[dict]:
    """Return the most recent workflow run summary (status, conclusion, created_at).
    None if no runs exist or API unavailable.
    """
    import requests
    token = _get_token()
    if not token:
        return None
    repo_name = _get_repo_name()
    url = (f"https://api.github.com/repos/{repo_name}"
           f"/actions/workflows/{workflow_file}/runs?per_page=1")
    try:
        r = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        runs = r.json().get("workflow_runs", [])
        if not runs:
            return None
        run = runs[0]
        return {
            "status": run.get("status"),         # queued / in_progress / completed
            "conclusion": run.get("conclusion"),  # success / failure / None
            "created_at": run.get("created_at"),
            "html_url": run.get("html_url"),
        }
    except Exception:
        return None
