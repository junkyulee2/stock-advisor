# Stock Advisor (페이퍼 트레이딩)

한국 주식(코스피+코스닥) 시총 상위 500 종목을 매일 자동 분석 → 점수화 →
Top 3 추천 / 매도 시그널 알림 까지 해주는 시스템.

**전략**: Momentum × Supply-Demand + Quality Guard
**철학**: 백테스트로 검증되기 전까지는 실매매 금지. 먼저 페이퍼 트레이딩.

---

## 아키텍처

```
[매일 저녁] GitHub Actions 가 클라우드에서
  ├─ 전일 종가까지의 데이터로 점수 계산
  ├─ Top 3 종목 → Discord 알림
  └─ 보유 중 종목 매도 시그널 체크

[PC / 핸드폰] Streamlit 앱
  ├─ 오늘 추천, 보유 현황, 거래 이력, 성과 대시보드
  └─ [가상매수] / [가상매도] 버튼
```

---

## 초심자 설치 가이드 (Windows)

### 1단계: Python 설치

1. https://www.python.org/downloads/ 접속
2. "Download Python 3.11.x" 클릭 (3.12 이상도 OK)
3. 설치 시 **"Add Python to PATH"** 반드시 체크
4. 완료 후 PowerShell 열어서 확인:
   ```
   python --version
   ```
   → `Python 3.11.x` 나와야 함

### 2단계: 이 프로젝트 폴더 세팅

PowerShell에서 이동:
```
cd "C:\Home AI\stock"
```

가상환경 만들기:
```
python -m venv venv
```

가상환경 활성화:
```
.\venv\Scripts\Activate.ps1
```
(프롬프트 앞에 `(venv)` 가 붙으면 성공)

만약 권한 에러 뜨면:
```
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

필요한 라이브러리 설치:
```
pip install -r requirements.txt
```
5~10분 걸림.

### 3단계: 첫 점수 계산 (로컬 테스트)

```
python run_daily.py --mode scores
```

정상이면 `data/scores/scores_YYYYMMDD.json` 파일이 생김.
에러 나면 알려줘.

### 4단계: Streamlit UI 실행

```
streamlit run app.py
```

브라우저가 자동으로 열리고 대시보드가 뜸.
핸드폰으로도 같은 Wi-Fi에서 접근 가능 (같은 네트워크에서 `http://<PC IP>:8501`).

### 5단계: Discord 웹훅 만들기

1. Discord 앱 → 서버 만들기 (혼자만 쓰는 서버)
2. 채널 설정 → 연동 → **웹후크** → "새 웹훅" → URL 복사
3. PowerShell에서 환경변수 설정:
   ```
   $env:DISCORD_WEBHOOK_URL = "여기에 URL 붙여넣기"
   ```
4. 이제 `python run_daily.py` 돌리면 Discord로 알림 옴

영구 저장 (PC 재시작해도 유지):
```
[System.Environment]::SetEnvironmentVariable("DISCORD_WEBHOOK_URL", "URL", "User")
```

### 6단계: GitHub 연동 (자동화)

1. https://github.com 계정 만들기 (없으면)
2. 새 repo 생성 (Private 추천): `stock-advisor`
3. 로컬에서 Git 초기화:
   ```
   cd "C:\Home AI\stock"
   git init
   git add .
   git commit -m "initial commit"
   git branch -M main
   git remote add origin https://github.com/<너의계정>/stock-advisor.git
   git push -u origin main
   ```
4. GitHub repo → Settings → **Secrets and variables** → Actions → New secret
   - Name: `DISCORD_WEBHOOK_URL`
   - Value: 웹훅 URL
5. Actions 탭 → "Daily Scores and Signals" 워크플로 활성화

이제 매일 장 마감 후 GitHub이 자동으로 점수 계산 + Discord 알림 + repo에 결과 저장.

### 7단계: Streamlit Cloud에 UI 배포 (핸드폰에서 접근)

1. https://streamlit.io/cloud 접속 → GitHub 계정으로 로그인
2. "New app" → repo 선택 → `app.py` 지정 → Deploy
3. **Advanced** → Secrets 에 추가:
   ```
   DISCORD_WEBHOOK_URL = "URL"
   ```
4. 배포 완료 후 URL 받아서 **핸드폰 브라우저 즐겨찾기** 추가
5. 외부 접근 차단 하려면 Streamlit Cloud의 인증 설정으로 제한

---

## 일상 사용 플로우

### 아침
1. Discord 알림 확인 → "오늘 Top3: A(92점), B(88점), C(86점)"
2. Streamlit 앱 열기 → [가상매수] 버튼 터치
3. 점수별 자동 투자금 제안 (95+ 30만, 90+ 20만, 85+ 10만)

### 장중·마감 후
- 매도 시그널 뜨면 Discord 알림 → 앱에서 [매도] 버튼
- 룰:
  - -15% 도달 → 즉시 전량 매도 (하드 손절)
  - +20% 도달 → 50% 익절
  - 20일 경과 → 타임 스톱
  - 매도점수 60+ → 50%, 80+ → 100%

### 주간 / 월간
- 성과 탭에서 누적 손익, 승률 확인
- 2주 뒤 승률·알파 검토 → 가중치 튜닝
- 백테스트 돌려서 KOSPI 대비 알파 확인

---

## 실매매로 넘어가는 조건

`config.yaml`의 `backtest.pass_criteria` 를 통과해야 함:

- KOSPI 대비 **알파 +3%p 이상** (연율)
- **MDD 15% 이내**
- **Sharpe 0.8 이상**
- **승률 50% 이상**

이걸 통과한 다음에도 **자본의 10%부터** 시작. 이게 "방어적 투입"의 원칙.

---

## 프로젝트 구조

```
c:\Home AI\stock\
├── config.yaml              # 모든 파라미터 (가중치, 룰)
├── requirements.txt
├── run_daily.py             # 일일 파이프라인
├── app.py                   # Streamlit UI
├── src/
│   ├── data_collector.py    # pykrx 데이터 수집
│   ├── indicators.py        # RSI, MACD, BB, MA, ADX
│   ├── scorer.py            # 4-팩터 점수
│   ├── sell_signals.py      # 매도 엔진
│   ├── portfolio.py         # 포지션 관리
│   ├── notifier.py          # Discord
│   ├── backtest.py          # 백테스트 (Phase 2)
│   └── utils.py
├── data/
│   ├── scores/              # 일별 점수 결과
│   ├── portfolio.json       # 현재 보유
│   └── history.json         # 거래 이력
├── .github/workflows/
│   └── daily_score.yml      # 클라우드 자동화
└── docs/
    └── 엔진_설명.md         # 점수 계산 원리 (쉬운 한글)
```

---

## 개선 로드맵

**Phase 1 (현재)**: MVP 엔진 + UI + 자동화
**Phase 2**: 백테스트 구현 → 5년 데이터로 검증 → 가중치 튜닝
**Phase 3**: 뉴스 센티먼트 (LLM 기반) 추가
**Phase 4**: 섹터 로테이션 / 상대강도 개선
**Phase 5**: 소액 실매매 (자본 10%)

---

## 문제 해결

**pykrx 설치 실패**:
- Python 버전 확인 (3.9~3.12 권장)
- `pip install --upgrade pip` 후 재시도

**Streamlit 실행 시 `ModuleNotFoundError`**:
- 가상환경 활성화 확인: `.\venv\Scripts\Activate.ps1`
- `pip install -r requirements.txt` 재실행

**GitHub Actions 실패**:
- Actions 탭에서 로그 확인
- Secrets에 `DISCORD_WEBHOOK_URL` 등록됐는지 확인

**한글 경로 이슈**:
- 이 프로젝트는 영문 경로 (`C:\Home AI\stock`)로 세팅됨

---

## 엔진 동작 원리

`docs/엔진_설명.md` 참고. 한글로 점수 계산 기준 설명.
