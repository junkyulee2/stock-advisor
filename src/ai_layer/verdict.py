"""AI verdict layer — Stage 2 (qualitative filter) + Stage 3 (self-critique)
fused into a single Claude Code call.

Input: list of candidate score rows (the 70+ slice of today's scores).
Output: per-ticker verdict {PASS, CAUTION, REJECT} with reasoning.

Recursive design (within a single API call to save tokens):
1. Initial judgment per candidate based on factor data + DART disclosures
2. Self-critique pass — explicitly check for confirmation bias, overreaction
3. Final verdict + confidence

The AI cannot upgrade the rule score, only block buys. UI uses the verdict
to gate the buy button; reasoning is always shown for transparency.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.utils import setup_logger

from . import budget, claude_cli, dart

logger = setup_logger(__name__)

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["PASS", "CAUTION", "REJECT"]},
                    "confidence": {"type": "number"},
                    "reasoning": {"type": "string"},
                    "red_flags": {"type": "array", "items": {"type": "string"}},
                    "self_critique_notes": {"type": "string"},
                },
                "required": ["ticker", "verdict", "confidence", "reasoning"],
            },
        },
        "batch_summary": {"type": "string"},
    },
    "required": ["verdicts"],
}


SYSTEM_PROMPT = """당신은 한국 주식 추천 시스템의 정성(qualitative) 거부권 필터다.

역할: 거부권 전용. 매수를 추천할 수 없다 — 막거나 통과시킬 뿐.
- PASS: 정성적 위험 신호 없음
- CAUTION: 신호 모호 또는 부분적 우려 — 매수 허용하되 경고 표시
- REJECT: 거버넌스/공시/구조적 위험에 대한 명확한 근거

판단 자료:
- 최근 30일 DART 공시 — 특히 위험 분류된 항목
- 팩터 점수는 "맥락"으로만 참고. 점수가 높다고 PASS, 낮다고 REJECT 아님.
- 동일 종목의 직전 판단 이력 (recursive memory)

해선 안 되는 것:
- 가격 방향 예측
- 알파 생성
- 매수 추천
- 룰 점수를 위로 보정

판단 절차 (단일 응답 내에서 두 단계 모두 수행):
1단계 — 초기 판단: 각 종목별 PASS/CAUTION/REJECT
2단계 — 자기비판: 본인 1단계 결정을 비판적으로 재검토
   · "REJECT가 단일 노이즈 신호에 과민반응한 것 아닌가?" → CAUTION으로 상향 검토
   · "PASS가 명백한 red flag를 놓친 것 아닌가?" → CAUTION/REJECT로 하향 검토
   · "직전 7일 비슷한 패턴에서 본인이 틀렸던 적 있는가?"
   self_critique_notes에 보정 사유 기록.

confidence (0.0~1.0): 판단의 견고함. 데이터 부족 = 낮은 confidence + CAUTION.

출력은 JSON 스키마 준수. reasoning은 한국어 1~2문장."""


# ---------- input builder ----------

def _candidate_payload(
    row: dict,
    *,
    disclosures: list[dict],
    past_verdicts: list[dict],
) -> dict:
    return {
        "ticker": row["ticker"],
        "name": row.get("name"),
        "market": row.get("market"),
        "rule_score": float(row.get("total_score", 0)),
        "factors": {
            "momentum": round(float(row.get("momentum_score", 0)), 1),
            "supply_demand": round(float(row.get("supply_demand_score", 0)), 1),
            "quality": round(float(row.get("quality_score", 0)), 1),
            "volatility": round(float(row.get("volatility_score", 0)), 1),
            "mean_reversion": round(float(row.get("mean_reversion_score", 0)), 1),
        },
        "regime": row.get("regime"),
        "disclosures_30d": dart.summarize_for_prompt(disclosures, max_items=12),
        "past_verdicts_7d": past_verdicts[:7],
    }


def build_user_prompt(payloads: list[dict]) -> str:
    head = (
        f"오늘({datetime.now().strftime('%Y-%m-%d')}) 룰 엔진이 점수 70+로 추출한 "
        f"후보 {len(payloads)}종목이다. 각각에 대해 정성 판단(PASS/CAUTION/REJECT) 내려라.\n\n"
        "판단 후 자기비판을 수행하고, 그 결과를 self_critique_notes에 적는다.\n\n"
        "후보 목록 (JSON):\n"
    )
    return head + json.dumps(payloads, ensure_ascii=False, indent=2)


# ---------- past-verdict memory ----------

def _verdicts_dir(config: dict) -> Path:
    p = config.get("paths", {}).get("ai_verdicts_dir", "data/ai_verdicts")
    return Path(p)


def load_recent_verdicts(config: dict, ticker: str, days: int = 7) -> list[dict]:
    """Pull this ticker's verdicts from the last N daily files."""
    d = _verdicts_dir(config)
    if not d.exists():
        return []
    files = sorted(d.glob("verdicts_*.json"), reverse=True)[:days]
    out: list[dict] = []
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for v in data.get("verdicts", []) or []:
            if v.get("ticker") == ticker:
                out.append({
                    "date": data.get("as_of"),
                    "verdict": v.get("verdict"),
                    "reasoning": v.get("reasoning", "")[:160],
                })
                break
    return out


def save_verdicts(config: dict, batch: dict, *, as_of: str) -> Path:
    d = _verdicts_dir(config)
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"verdicts_{as_of}.json"
    record = {
        "as_of": as_of,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "verdicts": batch.get("verdicts", []),
        "batch_summary": batch.get("batch_summary", ""),
    }
    fp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


# ---------- main entry ----------

def evaluate(
    config: dict,
    candidates: list[dict],
    *,
    persist: bool = True,
) -> dict[str, dict]:
    """Run AI verdict on the given candidate rows.

    Returns {ticker: verdict_dict}. On budget exhaustion or AI error, returns
    {} and the caller should fall back to score-only mode.

    Side effects (when persist=True):
    - Writes data/ai_verdicts/verdicts_YYYYMMDD.json
    - Records token usage to data/ai_usage.json
    """
    ai_cfg = config.get("ai_layer", {})
    if not ai_cfg.get("enabled", True):
        logger.info("ai_layer disabled in config; skipping verdict")
        return {}

    min_score = float(ai_cfg.get("min_score_for_ai", 70))
    max_n = int(ai_cfg.get("max_candidates_per_run", 50))

    # Filter + bound
    filtered = [c for c in candidates if float(c.get("total_score", 0)) >= min_score]
    filtered.sort(key=lambda x: -float(x.get("total_score", 0)))
    filtered = filtered[:max_n]
    if not filtered:
        return {}

    # Pre-flight budget check
    try:
        budget.check_budget(config)
    except budget.BudgetExceeded as e:
        logger.warning(f"AI budget exceeded; falling back: {e}")
        return {}

    # Disclosures (DART) — empty dict if no API key
    tickers = [c["ticker"] for c in filtered]
    disclosures_map = dart.disclosures_for_candidates(config, tickers)

    # Past verdicts (recursive memory)
    payloads = []
    for c in filtered:
        payloads.append(_candidate_payload(
            c,
            disclosures=disclosures_map.get(c["ticker"], []),
            past_verdicts=load_recent_verdicts(config, c["ticker"], days=7),
        ))

    user_prompt = build_user_prompt(payloads)

    logger.info(f"AI verdict call: {len(payloads)} candidates")
    try:
        result = claude_cli.call(
            config,
            user_prompt=user_prompt,
            system_prompt=SYSTEM_PROMPT,
            json_schema=VERDICT_SCHEMA,
        )
    except RuntimeError as e:
        # claude CLI missing (e.g., running in GitHub Actions without it installed).
        # Fail open — caller falls back to score-only.
        logger.warning(f"AI verdict skipped: {e}")
        return {}

    # Always record what we spent — even on error, tokens are gone
    budget.record_usage(
        config,
        input_tokens=result.input_tokens
                     + result.cache_creation_tokens
                     + result.cache_read_tokens,
        output_tokens=result.output_tokens,
        candidates_n=len(payloads),
        note="verdict.evaluate" + (" [ERROR]" if not result.ok else ""),
    )

    if not result.ok or not result.structured:
        logger.warning(f"AI verdict failed: {result.error}")
        return {}

    batch = result.structured
    verdict_list = batch.get("verdicts", []) or []

    # Map by ticker for easy lookup downstream
    by_ticker: dict[str, dict] = {}
    for v in verdict_list:
        tk = v.get("ticker")
        if tk:
            by_ticker[tk] = v

    if persist:
        as_of = datetime.now().strftime("%Y%m%d")
        save_verdicts(config, batch, as_of=as_of)

    logger.info(
        f"AI verdict done: {len(by_ticker)} verdicts, "
        f"{result.total_tokens:,} tokens, {result.duration_ms} ms"
    )
    return by_ticker


def latest_verdicts(config: dict) -> dict[str, dict]:
    """Load most-recent saved verdict file. Returns {ticker: verdict_dict}."""
    d = _verdicts_dir(config)
    if not d.exists():
        return {}
    files = sorted(d.glob("verdicts_*.json"), reverse=True)
    if not files:
        return {}
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for v in data.get("verdicts", []) or []:
        tk = v.get("ticker")
        if tk:
            out[tk] = v
    return out
