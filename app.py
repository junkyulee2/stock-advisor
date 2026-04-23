"""Streamlit UI — paper trading dashboard.

Tabs:
  1) Today's Recommendations (Top N with buy buttons)
  2) Positions (currently held, sell buttons)
  3) History (closed trades, P&L)
  4) Performance (equity curve, win rate)
  5) Backtest (view backtest results)
  6) Settings (config preview, regime info)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.utils import PROJECT_ROOT, load_config, load_json, save_json, iso_today
from src import portfolio as pf
from src import data_collector as dc
from src import sell_signals as ss

st.set_page_config(
    page_title="Stock Advisor",
    page_icon="📈",
    layout="wide",
)

CONFIG = load_config()
PORTFOLIO_PATH = PROJECT_ROOT / CONFIG["paths"]["portfolio"]
HISTORY_PATH = PROJECT_ROOT / CONFIG["paths"]["history"]
SCORES_DIR = PROJECT_ROOT / CONFIG["paths"]["scores_dir"]


# ---------- helpers ----------
def latest_scores_file() -> Path | None:
    if not SCORES_DIR.exists():
        return None
    files = sorted(SCORES_DIR.glob("scores_*.json"))
    return files[-1] if files else None


def load_latest_scores() -> pd.DataFrame:
    f = latest_scores_file()
    if not f:
        return pd.DataFrame()
    df = pd.read_json(f)
    return df


def get_current_price_safe(ticker: str) -> float | None:
    """Best-effort current price via pykrx (most recent trading day close)."""
    try:
        from datetime import timedelta
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        df = dc.get_ohlcv(ticker, start, end)
        if df.empty:
            return None
        return float(df["close"].iloc[-1])
    except Exception:
        return None


def save_portfolio_and_history(portfolio, history):
    save_json(PORTFOLIO_PATH, portfolio)
    save_json(HISTORY_PATH, history)


# ---------- session state ----------
if "portfolio" not in st.session_state:
    st.session_state.portfolio = pf.load_portfolio(PORTFOLIO_PATH)
if "history" not in st.session_state:
    st.session_state.history = pf.load_history(HISTORY_PATH)


# ---------- header ----------
st.title("📈 Stock Advisor (Paper Trading)")
st.caption("Momentum × Supply-Demand + Quality Guard")

summary_col = st.container()
summary = pf.compute_summary(
    st.session_state.portfolio,
    st.session_state.history,
    current_prices={},
)
c1, c2, c3, c4 = summary_col.columns(4)
c1.metric("보유 종목", summary["open_positions"])
c2.metric("누적 실현손익", f"{summary['realized_pnl_krw']:,.0f}원")
c3.metric("총 거래 수", summary["trades_count"])
c4.metric("승률", f"{summary['win_rate']*100:.1f}%")

st.divider()

# ---------- tabs ----------
tab_recs, tab_pos, tab_hist, tab_perf, tab_bt, tab_cfg = st.tabs(
    ["🎯 오늘의 추천", "📦 보유 중", "📜 거래이력", "📊 성과", "🧪 백테스트", "⚙️ 설정"]
)


# === TAB 1: Recommendations ===
with tab_recs:
    st.subheader("오늘의 추천 Top 종목")
    f = latest_scores_file()
    if not f:
        st.info("아직 계산된 점수가 없어요. `python run_daily.py --mode scores` 를 실행하세요.")
    else:
        st.caption(f"기준 파일: `{f.name}`")
        df = load_latest_scores()
        df = df.rename(columns={"index": "ticker"}) if "index" in df.columns else df

        min_score = CONFIG["portfolio_limits"]["min_score_to_buy"]
        eligible = df[df["total_score"] >= min_score].head(10)
        if eligible.empty:
            st.warning(f"{min_score}점 이상 종목이 없습니다. 오늘은 관망.")
        else:
            for i, row in eligible.iterrows():
                ticker = row["ticker"]
                held = ticker in st.session_state.portfolio["positions"]
                with st.container(border=True):
                    colA, colB, colC, colD = st.columns([3, 2, 2, 2])
                    colA.markdown(
                        f"**{row.get('name','')}** (`{ticker}`)  \n"
                        f"총점 **{row['total_score']:.1f}**  |  "
                        f"추천 투자금 **{int(row.get('amount_krw',0)):,}원**"
                    )
                    colB.markdown(
                        f"모멘텀 {row.get('momentum_score',0):.0f}  \n"
                        f"수급 {row.get('supply_demand_score',0):.0f}"
                    )
                    colC.markdown(
                        f"퀄리티 {row.get('quality_score',0):.0f}  \n"
                        f"역추세 {row.get('mean_reversion_score',0):.0f}"
                    )
                    if held:
                        colD.success("보유 중")
                    else:
                        amount = int(row.get("amount_krw", 0))
                        if amount > 0 and colD.button(
                            f"💰 {amount:,}원 가상매수",
                            key=f"buy_{ticker}_{i}",
                        ):
                            price = float(row.get("close", 0)) or get_current_price_safe(ticker)
                            if not price:
                                st.error("현재가를 가져올 수 없습니다.")
                            else:
                                try:
                                    pos = pf.buy(
                                        st.session_state.portfolio,
                                        ticker=ticker,
                                        name=row.get("name", ""),
                                        price=price,
                                        amount_krw=amount,
                                        score=float(row["total_score"]),
                                    )
                                    pf.record_buy_history(st.session_state.history, pos)
                                    save_portfolio_and_history(
                                        st.session_state.portfolio,
                                        st.session_state.history,
                                    )
                                    st.success(f"{row.get('name','')} 매수 완료: {pos['qty']}주 @ {price:,.0f}")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"매수 실패: {e}")


# === TAB 2: Positions ===
with tab_pos:
    st.subheader("보유 중인 종목")
    positions = st.session_state.portfolio["positions"]
    if not positions:
        st.info("보유 중인 종목이 없습니다.")
    else:
        for ticker, pos in list(positions.items()):
            cp = get_current_price_safe(ticker) or pos["entry_price"]
            ret_pct = (cp / pos["entry_price"] - 1) * 100
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
                c1.markdown(
                    f"**{pos['name']}** (`{ticker}`)  \n"
                    f"진입가 {pos['entry_price']:,.0f} · "
                    f"현재가 **{cp:,.0f}** · 수량 {pos['qty']}주"
                )
                color = "🟢" if ret_pct >= 0 else "🔴"
                c2.metric("수익률", f"{color} {ret_pct:+.2f}%")
                c3.markdown(
                    f"진입점수 {pos['entry_score']:.1f}  \n"
                    f"최고가 {pos.get('highest_price', pos['entry_price']):,.0f}"
                )
                # Check sell signal
                try:
                    from datetime import timedelta
                    end = datetime.now().strftime("%Y%m%d")
                    start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
                    pdf = dc.get_ohlcv(ticker, start, end)
                    fdf = dc.get_net_purchases(ticker, start, end)
                    decision = ss.decide_exit(pos, pdf, fdf, cp, CONFIG)
                except Exception:
                    decision = None

                if decision:
                    c4.warning(f"매도 시그널\n{decision['reason']}")
                    ratio = decision["sell_ratio"]
                    if c4.button(
                        f"🚨 {int(ratio*100)}% 매도",
                        key=f"sell_sig_{ticker}",
                    ):
                        try:
                            pf.sell(
                                st.session_state.portfolio,
                                st.session_state.history,
                                ticker=ticker,
                                price=cp,
                                sell_ratio=ratio,
                                reason=decision["reason"],
                            )
                            save_portfolio_and_history(
                                st.session_state.portfolio,
                                st.session_state.history,
                            )
                            st.success("매도 완료")
                            st.rerun()
                        except Exception as e:
                            st.error(f"매도 실패: {e}")
                else:
                    sell_ratio = c4.radio(
                        "수동 매도", ["0%", "50%", "100%"],
                        key=f"manual_{ticker}",
                        horizontal=True,
                    )
                    if sell_ratio != "0%" and c4.button("수동 매도 실행", key=f"msell_{ticker}"):
                        ratio = 0.5 if sell_ratio == "50%" else 1.0
                        try:
                            pf.sell(
                                st.session_state.portfolio,
                                st.session_state.history,
                                ticker=ticker,
                                price=cp,
                                sell_ratio=ratio,
                                reason="manual",
                            )
                            save_portfolio_and_history(
                                st.session_state.portfolio,
                                st.session_state.history,
                            )
                            st.success("매도 완료")
                            st.rerun()
                        except Exception as e:
                            st.error(f"매도 실패: {e}")


# === TAB 3: History ===
with tab_hist:
    st.subheader("거래 이력")
    trades = st.session_state.history.get("trades", [])
    if not trades:
        st.info("거래 기록이 없습니다.")
    else:
        df_hist = pd.DataFrame(trades)
        if "pnl_pct" in df_hist.columns:
            df_hist["pnl_pct"] = df_hist["pnl_pct"].round(2)
        st.dataframe(df_hist, use_container_width=True)


# === TAB 4: Performance ===
with tab_perf:
    st.subheader("성과 대시보드")
    trades = st.session_state.history.get("trades", [])
    sells = [t for t in trades if t["action"] == "sell"]
    if not sells:
        st.info("매도 거래가 아직 없습니다.")
    else:
        dfp = pd.DataFrame(sells)
        dfp["exit_date"] = pd.to_datetime(dfp["exit_date"])
        dfp = dfp.sort_values("exit_date")
        dfp["cum_pnl"] = dfp["pnl_krw"].cumsum()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dfp["exit_date"], y=dfp["cum_pnl"],
            mode="lines+markers", name="누적 실현손익(원)",
        ))
        fig.update_layout(
            title="누적 실현손익", xaxis_title="날짜", yaxis_title="원",
            template="plotly_white",
        )
        st.plotly_chart(fig, use_container_width=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("총 매도 건수", len(sells))
        wins = [t for t in sells if t["pnl_krw"] > 0]
        c2.metric("승률", f"{len(wins)/len(sells)*100:.1f}%")
        c3.metric("평균 수익률", f"{dfp['pnl_pct'].mean():.2f}%")


# === TAB 5: Backtest ===
with tab_bt:
    st.subheader("백테스트 결과")
    bt_dir = PROJECT_ROOT / CONFIG["paths"].get("backtest_results", "data/backtest")
    if not bt_dir.exists() or not any(bt_dir.iterdir()):
        st.info("백테스트 결과가 없습니다. (Phase 2에서 구현 예정)")
        st.code("python -m src.backtest  # TBD")
    else:
        st.write("결과 파일 목록:")
        for f in bt_dir.iterdir():
            st.write(f.name)


# === TAB 6: Settings ===
with tab_cfg:
    st.subheader("설정 (config.yaml)")
    st.caption("편집은 `config.yaml` 직접 수정 후 앱 재시작")
    st.json(CONFIG)
