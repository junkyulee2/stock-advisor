# Factor Research Snapshot — 2026-04-25

귀납적 팩터 리서치 결과. **1달 후 (2026-05-25경) 재검증 예정.**

## 방법론 요약

스크립트: [`tools/factor_research.py`](../tools/factor_research.py)

1. KOSPI+KOSDAQ 시총 Top 300 종목
2. 두 시점에서 features 측정 (T-30, T-15)
3. forward return = T-N → 현재 (last trading day)
4. Spearman IC = rank-corr(feature, forward_return)
5. 결과는 `data/research/factor_data_{N}d.csv`로 저장됨

## 측정한 팩터 (8개)

- `ret_5d`, `ret_20d`, `ret_60d` — 단/중/장기 수익률
- `ma20_dev`, `ma60_dev` — 이동평균 이격도
- `rsi` — RSI(14)
- `vol_20d` — 일일 수익률 표준편차 (변동성)
- `pct_b` — 볼린저 %B (20일)

## 2026-04-25 결과 (lookback 30d / 15d)

| 팩터 | IC 30d | IC 15d | 비고 |
|---|---|---|---|
| pct_b | **+0.191** | -0.007 | 30일 최강 (시스템에 없음) |
| ma20_dev | +0.185 | +0.016 | 새 역추세 로직 (장기만 효과) |
| rsi | +0.184 | -0.012 | 단기엔 0 |
| ret_20d | +0.176 | +0.090 | 모멘텀 핵심 |
| ret_5d | +0.174 | +0.095 | 단기 모멘텀 |
| **vol_20d** | +0.139 | **+0.311** | **단기 압도적 — 시스템에 없음** |
| ma60_dev | +0.131 | +0.086 | |
| ret_60d | +0.094 | +0.097 | 가장 약함 |

**시장 컨텍스트**: 강세장 (forward 30d return 평균 +12.7%, 15d +13.8%)

## 핵심 결론

1. **변동성(vol_20d) 추가 필요** — 단기 IC +0.311. 시스템에 없는 강한 시그널.
2. **ret_60d 비중 줄여야 함** — IC 0.09로 ret_5d/20d 대비 절반 수준.
3. **이격도(새 역추세)는 장기만 효과** — 30일 IC +0.185, 15일 +0.016. 가중치 유지 OK.
4. **pct_b는 검토 후보** — 30일 IC +0.191 강함. 다만 RSI/이격도와 상관 가능.

## 적용한 변경 (2026-04-25)

config.yaml:
```yaml
scoring.factors:
  momentum:       35   # 40 → 35
  supply_demand:  25   # 30 → 25
  quality:        15   # 20 → 15
  volatility:     10   # 신규
  mean_reversion: 15   # 10 → 15

scoring.momentum.return_weights:
  ret_5d_weight:  20   # 변경 없음
  ret_20d_weight: 65   # 50 → 65
  ret_60d_weight: 15   # 30 → 15
```

scorer.py:
- `compute_volatility_absolute()` 함수 추가 (백분위 기반)
- `combine_scores_absolute()`에 volatility_score 통합

## 한계 (caveat)

1. 단일 시점 분석 (lookback 2개만). 진짜 백테스트는 여러 시점 반복 필요.
2. **현재는 강세장** — 약세장에서 vol_20d는 음수 IC일 가능성.
3. IC 0.1~0.3은 약한 ~ 중간 시그널 (강력한 알파 아님).

## 1개월 후 재검증 절차

```bash
cd "c:/Home AI/stock"
venv\Scripts\python.exe tools/factor_research.py
```

`tools/factor_research.py`의 `NOW_DATE`를 그날 last trading day로 수정 후 실행.
결과를 본 문서와 비교:
- IC 부호가 같은가?
- vol_20d가 여전히 강한가?
- 다른 시장 환경에서도 일관성 있나?

만약 결과가 크게 다르면 (예: vol_20d IC가 음수로 변함):
- 강세장에만 통하는 시그널 → regime-aware weighting 필요
- 또는 변동성 가중치 줄이기

## 향후 개선 후보 (미구현)

- pct_b 추가 (30일 IC 가장 강함)
- 멀티 시점 백테스트 (10개 lookback × 6개월 history)
- 섹터별 IC 분석
- 약세장 데이터 모이면 regime-aware weight
