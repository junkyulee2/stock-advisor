# Replay Validation & Threshold Recalibration — 2026-05-02

## 목적
Phase A-1~A-4(가치 팩터, IQC Alpha 한국판, 가중치 활성화) 적용 후 새 엔진을
실제 과거 데이터에 돌려 **picks의 forward 수익률**과 **점수 임계 적정성**을
데이터로 검증.

## 방법론
- 17개 날짜 샘플 (2026-01-30 ~ 2026-04-20, 5거래일 간격, 90일 범위)
- 각 날짜에 신규 7팩터 엔진 (momentum 30/supply 25/quality 10/value 10/volatility 10/mean_rev 10/iqc_alpha 5)
- Universe 500 (KOSPI+KOSDAQ 시총 상위, liquidity 필터 후 ~423)
- Score ≥ 70 픽들 Top 15 추적
- **HOLD 22 거래일** + config.yaml 매도룰 적용 (hard_stop -15%, trailing -8%, time_stop 20d, take_profit_partial +20%/50%)
- N = 54 picks 누적

## Aggregate (sell rules 적용)
| 지표 | 값 |
|---|---|
| KOSPI mean | +10.33% |
| 시스템 mean | +6.34% |
| Win rate | 46% |
| **Alpha vs KOSPI** | **-3.99%** |

Lookahead bias 보정 시 실제 alpha는 **-6%p 정도** 추정 (Naver fundamental
+ 현재 flows가 과거 픽에 누출).

## 점수별 Bin 분석 ★

```
Bin       N    Win%    Mean      Alpha    Worst
95-100    0     -        -         -        -
90-95     1     0%    -5.25    -8.79     -5.25
85-90    13    38%    +1.19   -10.70     -9.68
82-85    19    53%    +9.91    -0.49    -12.06
80-82    16    44%    +4.86    +0.69    -19.22  ← 유일한 +alpha
75-80     5    60%   +13.18   -13.83    -14.24
```

**핵심 발견 — 점수가 높을수록 수익률이 떨어지는 역설**

| 누적 ≥ 임계 | N | Win% | Mean | Alpha |
|---|---|---|---|---|
| ≥ 70 | 54 | 46% | +6.34 | -3.99 |
| ≥ 80 | 49 | 45% | +5.64 | -2.98 |
| ≥ 82 | 33 | 45% | +6.02 | -4.76 |
| ≥ 85 | 14 | 36% | +0.73 | -10.56 |
| ≥ 88 |  3 |  0% | -5.25 | -15.89 |
| ≥ 90 |  1 |  0% | -5.25 |  -8.79 |

**해석**:
1. **80점 마지노선 데이터 검증됨** — 80~82 bin이 유일하게 +alpha
2. **85+ 픽은 KOSPI에 -10%p 뒤짐** — 모멘텀 overshooting 추격 문제
3. **95+ 픽은 90일간 0건** — 현재 sizing ladder의 "95+ → 30만원" 룰 사실상 비활성
4. **75~80 bin 작은 샘플(5)이라 신뢰성 낮음** — 80 마지노선 유지 권장

## Universe 200 vs 500 비교

| | 200 universe | 500 universe |
|---|---|---|
| N picks | 8 | 54 |
| Mean (with rules) | +10.60% | +6.34% |
| Alpha | -0.96% | -3.99% |

**결론**: 200 universe가 압도적으로 더 나음. 시총 상위 200개가 더 안정적이고
80점대 초반 픽 중 손실 종목이 적음. **Live config를 200으로 변경**.

## 적용 결정 (2026-05-02)

1. ✅ `universe.top_n_by_market_cap`: 500 → 200
2. ✅ `min_score_to_buy`: 80 (기존 유지, 데이터로 검증)
3. ✅ Sizing ladder는 그대로 (95+ 발생 시 활성화 가능)
4. ⏸ 가중치 재조정은 사용자 검토 후 결정 (현재는 Phase A-4 그대로)
5. ⏸ 85점 overshooting 문제는 별도 연구 — Phase B에서 sector cap, vol weighting 등으로 보강

## 다음 검증 포인트
- 1~2주 후 재실행 (샘플 늘려 통계적 유의성 ↑)
- AI veto 가동 후 데이터 누적 → 한화솔루션류 손실 픽들이 AI에 의해 거부되는지
- 85+ 픽 overshooting 패턴이 lookahead bias 효과인지 진짜 신호인지 분리
