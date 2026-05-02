"""AI veto post-mortem — 손실 픽들을 AI가 막았을지 사후 검증.

목적:
  Phase A 옵션 B (2026-05-02 결정). 신규 7팩터 시스템이 한화솔루션, 코오롱,
  솔루스첨단소재 같은 손실 종목을 추천했음. AI veto가 그 시점에 가동 중이었다면
  REJECT 했을지 검증 → AI 안전망의 실효성 측정.

방법:
  1. data/research/replay_*.csv 또는 portfolio.json에서 historical 픽 수집
  2. 각 픽의 entry 날짜 기준으로:
     - 신규 시스템으로 점수 재계산 (compute_daily_scores at asof)
     - DART 공시를 그 날짜 기준 30일 lookback (asof_date 파라미터로 lookahead 방지)
     - Claude CLI에 verdict 호출
     - PASS/CAUTION/REJECT 기록
  3. 집계: losers의 몇 %가 REJECT? winners의 몇 %가 잘못된 REJECT?

실행:
  python tools/ai_veto_postmortem.py
  PC 전용 — Claude Code CLI 필요. GitHub Actions에선 동작 안 함.

전제:
  - DART_API_KEY 환경변수 (.env 또는 시스템 env) 또는 config.yaml 설정
  - Claude Code Max 로그인 (`claude login`)
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# UTF-8 stdout on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.utils import load_config, setup_logger
from src.ai_layer import verdict, dart, claude_cli, budget
from run_daily import compute_daily_scores

logger = setup_logger("ai_veto_postmortem")

UNIVERSE_LIMIT = 200    # match production
DART_LOOKBACK = 30      # days


def _load_picks_from_replay() -> pd.DataFrame:
    """Load historical picks from latest replay CSV."""
    replay_dir = PROJECT_ROOT / "data" / "research"
    csvs = sorted(replay_dir.glob("replay_*.csv"))
    if not csvs:
        raise FileNotFoundError(
            f"No replay_*.csv in {replay_dir}. Run tools/replay_validate.py first."
        )
    latest = csvs[-1]
    print(f"Loading picks from {latest.name}")
    df = pd.read_csv(latest, dtype={"asof": str, "ticker": str})
    df["ticker"] = df["ticker"].str.zfill(6)
    return df


def _evaluate_pick(config: dict, asof: str, ticker: str, name: str) -> dict | None:
    """Recompute scores at asof, fetch historical DART, run AI verdict.
    Returns verdict dict or None on failure."""
    # Score recomputation
    try:
        df = compute_daily_scores(config, asof, limit=UNIVERSE_LIMIT)
    except Exception as e:
        print(f"  scoring failed: {e}")
        return None
    if ticker not in df.index:
        print(f"  ticker not in scored universe at {asof}")
        return None
    row = df.loc[ticker].to_dict()
    row["ticker"] = ticker

    # Historical DART (asof-aware fetch)
    try:
        disclosures = dart.fetch_disclosures(
            config, ticker, days=DART_LOOKBACK, asof_date=asof,
        )
    except Exception as e:
        print(f"  DART fetch failed: {e}")
        disclosures = []

    # Build payload — no past_verdicts in post-mortem (no recursive memory at past dates)
    payload = verdict._candidate_payload(
        row,
        disclosures=disclosures,
        past_verdicts=[],
    )

    # Call Claude — single candidate per call (clean, isolated)
    user_prompt = verdict.build_user_prompt([payload])
    try:
        result = claude_cli.call(
            config,
            user_prompt=user_prompt,
            system_prompt=verdict.SYSTEM_PROMPT,
            json_schema=verdict.VERDICT_SCHEMA,
        )
    except RuntimeError as e:
        print(f"  Claude CLI error: {e}")
        return None

    # Always record budget
    budget.record_usage(
        config,
        input_tokens=result.input_tokens
                     + result.cache_creation_tokens
                     + result.cache_read_tokens,
        output_tokens=result.output_tokens,
        candidates_n=1,
        note="ai_veto_postmortem" + ("" if result.ok else " [ERROR]"),
    )

    if not result.ok or not result.structured:
        print(f"  AI call failed: {result.error}")
        return None

    verdicts_list = result.structured.get("verdicts") or []
    if not verdicts_list:
        print("  no verdicts returned")
        return None
    v = verdicts_list[0]

    return {
        "verdict": v.get("verdict"),
        "confidence": v.get("confidence"),
        "reasoning": v.get("reasoning", ""),
        "red_flags": v.get("red_flags") or [],
        "self_critique_notes": v.get("self_critique_notes", ""),
        "n_disclosures": len(disclosures),
        "tokens": result.total_tokens,
    }


def main():
    config = load_config()

    # Smoke test Claude CLI first
    print("Smoke test: Claude Code CLI 가동 가능?")
    smoke = claude_cli.smoke_test(config)
    if not smoke.ok:
        print(f"  ❌ FAIL: {smoke.error}")
        print("  → Claude Code CLI를 PATH에 추가하고 `claude login` 했는지 확인하세요.")
        sys.exit(1)
    print(f"  ✅ OK ({smoke.duration_ms}ms)")
    print()

    picks = _load_picks_from_replay()
    print(f"전체 픽: {len(picks)}건")
    print()

    results = []
    for idx, pick in picks.iterrows():
        asof = str(pick["asof"]).zfill(8)
        ticker = str(pick["ticker"]).zfill(6)
        name = str(pick.get("name", ""))
        score = float(pick.get("score", 0))
        actual_ret = float(pick.get("sim_return_pct", pick.get("naive_return_pct", 0)))

        print(f"[{idx + 1}/{len(picks)}] {ticker} {name[:14]:<14} "
              f"asof={asof}  score={score:.1f}  actual={actual_ret:+.2f}%")

        v = _evaluate_pick(config, asof, ticker, name)
        if v is None:
            continue

        results.append({
            "asof": asof,
            "ticker": ticker,
            "name": name,
            "score": score,
            "actual_return_pct": actual_ret,
            "ai_verdict": v["verdict"],
            "ai_confidence": v["confidence"],
            "ai_reasoning": v["reasoning"][:200],
            "ai_red_flags": "; ".join(v["red_flags"][:5]),
            "n_disclosures": v["n_disclosures"],
            "tokens": v["tokens"],
        })

        flag = "🚨" if v["verdict"] == "REJECT" else ("⚠️" if v["verdict"] == "CAUTION" else "✅")
        print(f"  {flag} AI: {v['verdict']} (conf {v['confidence']:.2f})")
        print(f"     {v['reasoning'][:120]}")
        if v["red_flags"]:
            print(f"     red_flags: {', '.join(v['red_flags'][:3])}")
        print()

    if not results:
        print("결과 없음.")
        return

    res = pd.DataFrame(results)
    print("=" * 78)
    print("AGGREGATE — AI veto가 손실 픽을 막았을까?")
    print("=" * 78)

    losers = res[res["actual_return_pct"] < 0]
    winners = res[res["actual_return_pct"] > 0]

    print(f"\n총 {len(res)}건 평가됨")

    if not losers.empty:
        loser_mean = losers["actual_return_pct"].mean()
        print(f"\n--- LOSERS ({len(losers)}건, 평균 {loser_mean:+.2f}%) ---")
        vc = losers["ai_verdict"].value_counts()
        for verdict_name in ("REJECT", "CAUTION", "PASS"):
            n = int(vc.get(verdict_name, 0))
            pct = n / len(losers) * 100 if len(losers) else 0
            print(f"  {verdict_name:<8} {n:>3}건 ({pct:5.1f}%)")
        rejected_n = int(vc.get("REJECT", 0))
        cautioned_n = int(vc.get("CAUTION", 0))
        defended_n = rejected_n + cautioned_n  # CAUTION도 사용자가 보고 결정 가능
        print(f"\n  ★ AI 방어율 (REJECT+CAUTION): {defended_n}/{len(losers)} "
              f"= {defended_n/len(losers)*100:.0f}%")

    if not winners.empty:
        win_mean = winners["actual_return_pct"].mean()
        print(f"\n--- WINNERS ({len(winners)}건, 평균 {win_mean:+.2f}%) ---")
        vc = winners["ai_verdict"].value_counts()
        for verdict_name in ("PASS", "CAUTION", "REJECT"):
            n = int(vc.get(verdict_name, 0))
            pct = n / len(winners) * 100 if len(winners) else 0
            print(f"  {verdict_name:<8} {n:>3}건 ({pct:5.1f}%)")
        false_neg = winners[winners["ai_verdict"] == "REJECT"]
        if not false_neg.empty:
            print(f"\n  ⚠️ AI가 잘못 거부한 winner: {len(false_neg)}건 (false negative)")
            for _, r in false_neg.iterrows():
                print(f"    {r['ticker']} {r['name'][:14]:<14} {r['actual_return_pct']:+.2f}%")

    # Save
    out_dir = PROJECT_ROOT / "data" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ai_veto_postmortem_{datetime.now():%Y%m%d}.csv"
    res.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\n저장: {out_path}")


if __name__ == "__main__":
    main()
