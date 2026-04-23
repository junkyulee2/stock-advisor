"""Streamlit UI — paper trading dashboard (diagnostic mode).

Wrapped in try/except so errors surface in the browser, not as silent crashes.
"""
from __future__ import annotations

import traceback
import streamlit as st

st.set_page_config(page_title="Stock Advisor", page_icon="📈", layout="wide")

# ==================================================
#  Step 1: confirm Streamlit boots
# ==================================================
st.title("📈 Stock Advisor")
st.caption("Cloud MVP — diagnostic boot")

# ==================================================
#  Step 2: try imports one by one
# ==================================================
imports_ok = True
with st.status("Loading modules...", expanded=True) as status:
    try:
        import json
        from datetime import datetime
        from pathlib import Path
        st.write("✓ stdlib")
    except Exception as e:
        st.error(f"stdlib import failed: {e}")
        imports_ok = False

    try:
        import pandas as pd
        st.write(f"✓ pandas {pd.__version__}")
    except Exception as e:
        st.error(f"pandas: {e}")
        imports_ok = False

    try:
        import plotly.graph_objects as go
        st.write("✓ plotly")
    except Exception as e:
        st.error(f"plotly: {e}")
        imports_ok = False

    try:
        from src.utils import PROJECT_ROOT, load_config, load_json, save_json, iso_today
        st.write(f"✓ src.utils (PROJECT_ROOT={PROJECT_ROOT})")
    except Exception as e:
        st.error(f"src.utils: {e}")
        st.code(traceback.format_exc())
        imports_ok = False

    try:
        from src import portfolio as pf
        st.write("✓ src.portfolio")
    except Exception as e:
        st.error(f"src.portfolio: {e}")
        st.code(traceback.format_exc())
        imports_ok = False

    try:
        from src import data_collector as dc
        st.write("✓ src.data_collector")
    except Exception as e:
        st.error(f"src.data_collector: {e}")
        st.code(traceback.format_exc())
        imports_ok = False

    try:
        from src import sell_signals as ss
        st.write("✓ src.sell_signals")
    except Exception as e:
        st.error(f"src.sell_signals: {e}")
        st.code(traceback.format_exc())
        imports_ok = False

    if imports_ok:
        status.update(label="✓ All modules loaded", state="complete")
    else:
        status.update(label="✗ Import errors — see above", state="error")

if not imports_ok:
    st.stop()

# ==================================================
#  Step 3: confirm config + paths
# ==================================================
try:
    CONFIG = load_config()
    PORTFOLIO_PATH = PROJECT_ROOT / CONFIG["paths"]["portfolio"]
    HISTORY_PATH = PROJECT_ROOT / CONFIG["paths"]["history"]
    SCORES_DIR = PROJECT_ROOT / CONFIG["paths"]["scores_dir"]
    st.success(f"✓ config loaded — {len(CONFIG.get('scoring', {}))} scoring sections")
except Exception as e:
    st.error("Config load failed:")
    st.code(traceback.format_exc())
    st.stop()


# ==================================================
#  Step 4: portfolio + history state
# ==================================================
if "portfolio" not in st.session_state:
    try:
        st.session_state.portfolio = pf.load_portfolio(PORTFOLIO_PATH)
    except Exception as e:
        st.error("Load portfolio failed:")
        st.code(traceback.format_exc())
        st.stop()
if "history" not in st.session_state:
    try:
        st.session_state.history = pf.load_history(HISTORY_PATH)
    except Exception as e:
        st.error("Load history failed:")
        st.code(traceback.format_exc())
        st.stop()

# ==================================================
#  Step 5: show summary
# ==================================================
portfolio = st.session_state.portfolio
history = st.session_state.history
summary = pf.compute_summary(portfolio, history, current_prices={})

c1, c2, c3, c4 = st.columns(4)
c1.metric("보유 종목", summary["open_positions"])
c2.metric("실현 손익", f"{summary['realized_pnl_krw']:,.0f}원")
c3.metric("거래 수", summary["trades_count"])
c4.metric("승률", f"{summary['win_rate']*100:.1f}%")

st.divider()

# ==================================================
#  Step 6: latest scores (if any)
# ==================================================
st.subheader("오늘의 추천")

def latest_scores_file():
    if not SCORES_DIR.exists():
        return None
    files = sorted(SCORES_DIR.glob("scores_*.json"))
    return files[-1] if files else None

f = latest_scores_file()
if not f:
    st.info("점수 파일이 아직 없습니다. GitHub Actions 워크플로를 활성화하면 매일 저녁 자동 계산됩니다.")
else:
    st.caption(f"기준 파일: `{f.name}`")
    try:
        df = pd.read_json(f)
        if "total_score" in df.columns:
            df = df.sort_values("total_score", ascending=False)
        min_score = CONFIG["portfolio_limits"]["min_score_to_buy"]
        top = df[df["total_score"] >= min_score].head(10)
        if top.empty:
            st.warning(f"{min_score}점 이상 종목이 없습니다.")
        else:
            st.dataframe(
                top[["ticker", "name", "total_score", "momentum_score",
                     "supply_demand_score", "quality_score", "mean_reversion_score",
                     "close", "amount_krw"]],
                use_container_width=True,
            )
    except Exception as e:
        st.error("Scores load failed:")
        st.code(traceback.format_exc())

st.divider()
st.subheader("보유 종목")
if not portfolio["positions"]:
    st.info("보유 종목 없음")
else:
    rows = []
    for tic, p in portfolio["positions"].items():
        rows.append({
            "티커": tic, "종목": p["name"],
            "진입가": p["entry_price"], "수량": p["qty"],
            "원가": p["cost_krw"], "진입일": p["entry_date"],
            "mode": p.get("mode", "simulation"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

st.caption("Cloud MVP · desktop app has full buy/sell controls.")
