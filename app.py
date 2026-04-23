"""Streamlit cloud UI — paper trading dashboard.

Errors are surfaced in-browser via try/except wrappers.
Simpler than the PyQt desktop app (no buy/sell buttons yet) — read-only view.
"""
from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="춘큐 스탁 어드바이져", page_icon="📈", layout="wide")

# --- CSS polish ---
st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    [data-testid="stMetricValue"] { font-size: 24px; font-weight: 700; }
    .stDataFrame { border-radius: 8px; overflow: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Imports (safe) ---
try:
    from src.utils import PROJECT_ROOT, load_config
    from src import portfolio as pf
except Exception:
    st.error("앱 초기화 실패")
    st.code(traceback.format_exc())
    st.stop()


@st.cache_data(ttl=300)
def cached_config():
    return load_config()


CONFIG = cached_config()
PORTFOLIO_PATH = PROJECT_ROOT / CONFIG["paths"]["portfolio"]
HISTORY_PATH = PROJECT_ROOT / CONFIG["paths"]["history"]
SCORES_DIR = PROJECT_ROOT / CONFIG["paths"]["scores_dir"]

# --- Header ---
col_title, col_subtitle = st.columns([2, 3])
with col_title:
    st.markdown("## 📈 춘큐 스탁 어드바이져")
with col_subtitle:
    st.caption("Momentum × Supply-Demand + Quality Guard · Cloud MVP")

# --- Summary metrics ---
portfolio = pf.load_portfolio(PORTFOLIO_PATH)
history = pf.load_history(HISTORY_PATH)
summary = pf.compute_summary(portfolio, history, current_prices={})

m1, m2, m3, m4 = st.columns(4)
m1.metric("보유 종목", summary["open_positions"])
m2.metric("실현 손익", f"{summary['realized_pnl_krw']:+,.0f}원")
m3.metric("거래 수", summary["trades_count"])
m4.metric("승률", f"{summary['win_rate']*100:.1f}%")

st.divider()


# --- Helpers ---
def latest_scores_file() -> Path | None:
    if not SCORES_DIR.exists():
        return None
    files = sorted(SCORES_DIR.glob("scores_*.json"))
    return files[-1] if files else None


# --- Tabs ---
tab_rec, tab_pos, tab_hist, tab_info = st.tabs(
    ["🎯 오늘의 추천", "📦 보유", "📜 거래이력", "ℹ️ 정보"]
)


# === TAB: recommendations ===
with tab_rec:
    f = latest_scores_file()
    if not f:
        st.info(
            "점수 파일이 아직 없어요.\n\n"
            "매일 점수를 자동으로 계산하려면 GitHub Actions 워크플로를 활성화하세요. "
            "(데스크탑 앱에서는 '오늘 점수 계산' 버튼으로 즉시 가능합니다.)"
        )
    else:
        st.caption(f"기준: `{f.name}`")
        try:
            df = pd.read_json(f)
            if "total_score" in df.columns:
                df = df.sort_values("total_score", ascending=False)
            min_score = CONFIG["portfolio_limits"]["min_score_to_buy"]
            top = df.head(10).copy()
            top["amount_krw"] = top["amount_krw"].map(lambda x: f"{int(x):,}원" if x else "-")
            cols = [
                "ticker", "name", "total_score",
                "momentum_score", "supply_demand_score",
                "quality_score", "mean_reversion_score",
                "close", "amount_krw",
            ]
            available = [c for c in cols if c in top.columns]
            st.dataframe(
                top[available],
                use_container_width=True, hide_index=True,
                column_config={
                    "total_score": st.column_config.NumberColumn("총점", format="%.1f"),
                    "momentum_score": st.column_config.NumberColumn("모멘텀", format="%.0f"),
                    "supply_demand_score": st.column_config.NumberColumn("수급", format="%.0f"),
                    "quality_score": st.column_config.NumberColumn("퀄리티", format="%.0f"),
                    "mean_reversion_score": st.column_config.NumberColumn("역추세", format="%.0f"),
                    "close": st.column_config.NumberColumn("현재가", format="%d원"),
                    "amount_krw": st.column_config.TextColumn("배정"),
                },
            )
            st.caption(f"💡 매수/매도 버튼은 데스크탑 앱에서 사용 · 85점 이상만 추천")
        except Exception:
            st.error("점수 파일 로드 실패")
            st.code(traceback.format_exc())


# === TAB: positions ===
with tab_pos:
    if not portfolio["positions"]:
        st.info("보유 종목 없음")
    else:
        rows = []
        for tic, p in portfolio["positions"].items():
            rows.append({
                "티커": tic,
                "종목": p["name"],
                "진입가": p["entry_price"],
                "수량": p["qty"],
                "원가": p["cost_krw"],
                "진입일": p["entry_date"],
                "mode": p.get("mode", "simulation"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# === TAB: history ===
with tab_hist:
    trades = history.get("trades", [])
    if not trades:
        st.info("거래 기록 없음")
    else:
        st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)


# === TAB: info ===
with tab_info:
    st.markdown("#### 시스템 정보")
    st.write(f"- Project root: `{PROJECT_ROOT}`")
    st.write(f"- Scores dir: `{SCORES_DIR}`")
    st.write(f"- Streamlit: {st.__version__}")
    import sys
    st.write(f"- Python: {sys.version.split()[0]}")
    st.markdown("---")
    st.markdown("#### 스코어링 가중치")
    w = CONFIG["scoring"]["factors"]
    cols = st.columns(4)
    cols[0].metric("모멘텀", f"{w['momentum']}%")
    cols[1].metric("수급", f"{w['supply_demand']}%")
    cols[2].metric("퀄리티", f"{w['quality']}%")
    cols[3].metric("역추세", f"{w['mean_reversion']}%")
