"""Headless Claude Code CLI wrapper.

Uses the user's existing Claude Code Max subscription (OAuth) — no API key.
We invoke `claude -p --output-format=json` and parse the structured response.

Important:
- `--bare` is NOT used because it requires ANTHROPIC_API_KEY. We rely on
  OAuth tokens stored by the user's Claude Code login.
- `--json-schema` is used to enforce structured output. Result lands in
  the `structured_output` field of the JSON response, not `result`.
- Token usage from CLI's `usage` block is recorded against the budget.

A baseline of ~25-35K cache_creation tokens is consumed per cold call
(Claude Code's coding system prompt being cached). This is the fixed cost
of using subscription mode vs API mode.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Optional

from src.utils import setup_logger

logger = setup_logger(__name__)

DEFAULT_MODEL = "claude-opus-4-7"


@dataclass
class CallResult:
    ok: bool
    structured: Optional[dict]      # parsed structured_output (when schema given)
    text: Optional[str]              # raw `result` text (when no schema)
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost_usd: float                  # CLI-reported (subscription = $0; API = real $)
    duration_ms: int
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )


def _resolve_cli(config: dict) -> str:
    cfg = config.get("ai_layer", {})
    explicit = cfg.get("claude_cli_path")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if not found:
        raise RuntimeError(
            "claude CLI not found in PATH. Set ai_layer.claude_cli_path in config.yaml "
            "or add it to PATH."
        )
    return found


def call(
    config: dict,
    *,
    user_prompt: str,
    system_prompt: str,
    json_schema: Optional[dict] = None,
    model: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> CallResult:
    """Invoke Claude Code CLI with the given prompts. Returns CallResult.

    Never raises on Claude-side errors — caller checks `result.ok`. Only
    raises on infrastructural problems (CLI missing, JSON parse failure).
    """
    cfg = config.get("ai_layer", {})
    cli = _resolve_cli(config)
    model = model or cfg.get("model") or DEFAULT_MODEL
    timeout = int(timeout_seconds or cfg.get("timeout_seconds", 180))

    args = [
        cli, "-p",
        "--output-format=json",
        "--model", model,
        "--no-session-persistence",   # each call independent — no stale context
        "--disable-slash-commands",   # skills/slash not needed; reduces tokens
        "--system-prompt", system_prompt,
    ]
    if json_schema is not None:
        args += ["--json-schema", json.dumps(json_schema, ensure_ascii=False)]

    try:
        proc = subprocess.run(
            args,
            input=user_prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CallResult(
            ok=False, structured=None, text=None,
            input_tokens=0, cache_creation_tokens=0, cache_read_tokens=0,
            output_tokens=0, cost_usd=0.0, duration_ms=timeout * 1000,
            error=f"timeout after {timeout}s",
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"claude CLI invocation failed: {e}") from e

    if not proc.stdout:
        return CallResult(
            ok=False, structured=None, text=None,
            input_tokens=0, cache_creation_tokens=0, cache_read_tokens=0,
            output_tokens=0, cost_usd=0.0, duration_ms=0,
            error=f"empty stdout. stderr={proc.stderr[:300]}",
        )

    try:
        d = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return CallResult(
            ok=False, structured=None, text=None,
            input_tokens=0, cache_creation_tokens=0, cache_read_tokens=0,
            output_tokens=0, cost_usd=0.0, duration_ms=0,
            error=f"invalid JSON from CLI: {e}. head={proc.stdout[:200]}",
        )

    usage = d.get("usage") or {}
    is_error = bool(d.get("is_error", False))
    err_msg = None
    if is_error:
        err_msg = d.get("result") or d.get("api_error_status") or "claude reported error"

    return CallResult(
        ok=not is_error,
        structured=d.get("structured_output"),
        text=d.get("result"),
        input_tokens=int(usage.get("input_tokens", 0)),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0)),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cost_usd=float(d.get("total_cost_usd", 0.0)),
        duration_ms=int(d.get("duration_ms", 0)),
        error=err_msg,
    )


def smoke_test(config: dict, model: Optional[str] = None) -> CallResult:
    """Cheap end-to-end check: are auth + CLI + JSON schema all working?"""
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    }
    return call(
        config,
        user_prompt='Reply with status "ok".',
        system_prompt="You are a smoke-test responder. Reply per schema.",
        json_schema=schema,
        model=model or "claude-haiku-4-5",  # cheap by default
        timeout_seconds=30,
    )
