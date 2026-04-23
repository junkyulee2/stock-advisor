"""춘큐 스탁 어드바이져 — pro fintech dashboard (Streamlit)."""
from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="춘큐 스탁 어드바이져",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
#  Safe imports
# ============================================================
try:
    from src.utils import PROJECT_ROOT, load_config
    from src import portfolio as pf
except Exception:
    st.error("초기화 실패")
    st.code(traceback.format_exc())
    st.stop()


@st.cache_data(ttl=300)
def cached_config():
    return load_config()


CONFIG = cached_config()
PORTFOLIO_PATH = PROJECT_ROOT / CONFIG["paths"]["portfolio"]
HISTORY_PATH = PROJECT_ROOT / CONFIG["paths"]["history"]
SCORES_DIR = PROJECT_ROOT / CONFIG["paths"]["scores_dir"]


# ============================================================
#  Global styling — heavy CSS injection
# ============================================================
def inject_css():
    st.markdown(
        """
        <style>
        /* Page background + typography */
        .stApp {
            background: #0b1222;
            color: #e5edff;
        }
        .block-container {
            max-width: 1400px;
            padding-top: 1.2rem !important;
            padding-bottom: 2rem !important;
        }

        /* Hide Streamlit chrome */
        #MainMenu, footer, header { visibility: hidden; }
        .stDeployButton { display: none; }

        /* Hero header */
        .hero {
            background: linear-gradient(135deg, #0f172a 0%, #111c33 50%, #0b1222 100%);
            border: 1px solid #22324f;
            border-radius: 14px;
            padding: 20px 26px;
            margin-bottom: 18px;
            display: flex;
            align-items: center;
            gap: 18px;
        }
        .hero-icon {
            width: 54px; height: 54px;
            border-radius: 12px;
            background: radial-gradient(circle at 30% 30%, #1e293b, #060b18);
            display: flex; align-items: center; justify-content: center;
            font-size: 28px;
            border: 1px solid #22324f;
        }
        .hero-title {
            font-size: 22px; font-weight: 800; color: #f0f5ff;
            letter-spacing: -0.5px; margin: 0;
        }
        .hero-sub {
            font-size: 12px; color: #9aa8c7; margin-top: 2px;
            letter-spacing: 0.2px;
        }
        .hero-right {
            margin-left: auto; text-align: right;
        }
        .hero-stamp {
            font-family: "Consolas", monospace;
            color: #9aa8c7; font-size: 11px;
        }

        /* Metric cards */
        .kpi {
            background: #111c33;
            border: 1px solid #1b2744;
            border-radius: 12px;
            padding: 14px 18px;
            transition: border-color .15s ease;
            height: 100%;
        }
        .kpi:hover { border-color: #22324f; }
        .kpi-label {
            font-size: 10.5px; color: #6b7a9c; letter-spacing: 0.9px;
            font-weight: 700; text-transform: uppercase;
        }
        .kpi-value {
            font-size: 24px; font-weight: 800; margin-top: 4px;
            color: #f0f5ff; font-variant-numeric: tabular-nums;
        }
        .kpi-green { color: #22c55e !important; }
        .kpi-red   { color: #ef4444 !important; }
        .kpi-gold  { color: #fbbf24 !important; }
        .kpi-blue  { color: #3b82f6 !important; }
        .kpi-hint {
            font-size: 10.5px; color: #6b7a9c; margin-top: 2px;
        }

        /* Stock card */
        .stock-card {
            background: #111c33;
            border: 1px solid #1b2744;
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 10px;
            transition: border-color .15s ease, transform .15s ease;
        }
        .stock-card:hover {
            border-color: #3b82f6;
            transform: translateY(-1px);
        }
        .stock-name {
            font-size: 16px; font-weight: 700; color: #f0f5ff;
        }
        .stock-ticker {
            font-family: "Consolas", monospace;
            font-size: 11px; color: #9aa8c7;
        }
        .big-score {
            font-size: 32px; font-weight: 900; color: #fbbf24;
            font-variant-numeric: tabular-nums;
            line-height: 1;
        }
        .small-label {
            font-size: 10px; color: #6b7a9c;
            text-transform: uppercase; letter-spacing: 0.8px;
            font-weight: 700;
        }
        .factor-row {
            display: flex; align-items: center; gap: 8px;
            margin: 3px 0; font-size: 11.5px;
        }
        .factor-name { width: 48px; color: #9aa8c7; }
        .factor-bar {
            flex: 1; height: 5px; background: #1b2744;
            border-radius: 3px; overflow: hidden;
        }
        .factor-fill { height: 100%; border-radius: 3px; }
        .factor-val {
            width: 28px; text-align: right; color: #f0f5ff;
            font-weight: 700; font-variant-numeric: tabular-nums;
        }

        /* Pills */
        .pill {
            display: inline-block; padding: 3px 10px;
            border-radius: 999px; font-size: 10.5px;
            font-weight: 700; letter-spacing: 0.3px;
        }
        .pill-green {
            background: rgba(34,197,94,0.15); color: #22c55e;
            border: 1px solid rgba(34,197,94,0.35);
        }
        .pill-red {
            background: rgba(239,68,68,0.15); color: #ef4444;
            border: 1px solid rgba(239,68,68,0.35);
        }
        .pill-blue {
            background: rgba(59,130,246,0.15); color: #3b82f6;
            border: 1px solid rgba(59,130,246,0.35);
        }
        .pill-gray {
            background: rgba(107,122,156,0.15); color: #9aa8c7;
            border: 1px solid rgba(107,122,156,0.35);
        }

        /* Tabs */
        .stTabs [data-baseweb="tab-list"] {
            gap: 2px; background: transparent;
            border-bottom: 1px solid #1b2744;
        }
        .stTabs [data-baseweb="tab"] {
            background: transparent;
            color: #9aa8c7; font-weight: 600;
            padding: 10px 18px; border-radius: 8px 8px 0 0;
        }
        .stTabs [aria-selected="true"] {
            background: #111c33;
            color: #f0f5ff;
            border: 1px solid #22324f; border-bottom-color: #111c33;
        }

        /* Dataframes */
        .stDataFrame { border-radius: 10px; overflow: hidden; }

        /* Plotly containers */
        .js-plotly-plot .plotly .bg { fill: #111c33 !important; }

        /* Empty state panel */
        .empty-panel {
            background: #111c33; border: 1px dashed #22324f;
            border-radius: 12px; padding: 40px; text-align: center;
            color: #9aa8c7;
        }
        .empty-emoji { font-size: 34px; margin-bottom: 6px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_css()


# ============================================================
#  Hero header
# ============================================================
now_kst = datetime.now().strftime("%Y-%m-%d %H:%M KST")
st.markdown(
    f"""
    <div class="hero">
        <div class="hero-icon">📈</div>
        <div>
            <div class="hero-title">춘큐 스탁 어드바이져</div>
            <div class="hero-sub">Momentum × Supply-Demand × Quality Guard · 페이퍼 트레이딩</div>
        </div>
        <div class="hero-right">
            <span class="pill pill-green">● LIVE</span>
            <div class="hero-stamp">{now_kst}</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
#  KPI strip
# ============================================================
portfolio = pf.load_portfolio(PORTFOLIO_PATH)
history = pf.load_history(HISTORY_PATH)
summary = pf.compute_summary(portfolio, history, current_prices={})


def kpi_card(label: str, value: str, color: str = "", hint: str = ""):
    cls = ""
    if color == "green": cls = "kpi-green"
    elif color == "red": cls = "kpi-red"
    elif color == "gold": cls = "kpi-gold"
    elif color == "blue": cls = "kpi-blue"
    return (
        f'<div class="kpi">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value {cls}">{value}</div>'
        f'<div class="kpi-hint">{hint}</div>'
        f'</div>'
    )


realized = summary["realized_pnl_krw"]
win_rate = summary["win_rate"] * 100
k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown(
        kpi_card("OPEN POSITIONS", f"{summary['open_positions']}", "blue",
                 f"실 보유 종목 수"),
        unsafe_allow_html=True,
    )
with k2:
    color = "green" if realized >= 0 else "red"
    sign = "+" if realized >= 0 else ""
    st.markdown(
        kpi_card("REALIZED P/L", f"{sign}{realized:,.0f}원", color,
                 "누적 실현손익"),
        unsafe_allow_html=True,
    )
with k3:
    wr_color = "green" if win_rate >= 50 else "red" if summary["closed_trades"] > 0 else ""
    wr_text = f"{win_rate:.1f}%" if summary["closed_trades"] > 0 else "–"
    st.markdown(
        kpi_card("WIN RATE", wr_text, wr_color,
                 f"{summary['wins']}승 / {summary['losses']}패"),
        unsafe_allow_html=True,
    )
with k4:
    st.markdown(
        kpi_card("TRADES", f"{summary['trades_count']}", "",
                 f"매수+매도 총 기록"),
        unsafe_allow_html=True,
    )

st.write("")  # spacing


# ============================================================
#  Helpers
# ============================================================
def latest_scores_file() -> Path | None:
    if not SCORES_DIR.exists():
        return None
    files = sorted(SCORES_DIR.glob("scores_*.json"))
    return files[-1] if files else None


FACTOR_META = [
    ("모멘텀", "momentum_score", "#22c55e"),
    ("수급",   "supply_demand_score", "#3b82f6"),
    ("퀄리티", "quality_score", "#a855f7"),
    ("역추세", "mean_reversion_score", "#fbbf24"),
]


def render_stock_card(rec: dict, held: bool = False):
    name = rec.get("name", "-")
    ticker = rec.get("ticker", "")
    score = float(rec.get("total_score", 0))
    price = int(rec.get("close", 0))
    amount = int(rec.get("amount_krw", 0))
    market = rec.get("market", "")

    # Factor rows
    factor_html = ""
    for fname, key, color in FACTOR_META:
        v = float(rec.get(key, 0) or 0)
        factor_html += (
            f'<div class="factor-row">'
            f'<span class="factor-name">{fname}</span>'
            f'<div class="factor-bar"><div class="factor-fill" '
            f'style="width:{max(0,min(100,v))}%;background:{color};"></div></div>'
            f'<span class="factor-val">{v:.0f}</span>'
            f'</div>'
        )

    # Status pill
    if held:
        pill = '<span class="pill pill-blue">● 보유중</span>'
    elif amount > 0:
        pill = f'<span class="pill pill-green">배정 {amount:,}원</span>'
    else:
        pill = '<span class="pill pill-gray">85점 미달</span>'

    st.markdown(
        f"""
        <div class="stock-card">
          <div style="display:flex; align-items:center; gap:24px;">
            <div style="flex:2; min-width:160px;">
              <div class="stock-name">{name}</div>
              <div class="stock-ticker">{ticker} · {market}</div>
              <div style="margin-top:8px;">{pill}</div>
            </div>
            <div style="flex:0 0 90px; text-align:center;">
              <div class="small-label">SCORE</div>
              <div class="big-score">{score:.1f}</div>
            </div>
            <div style="flex:3; min-width:220px;">
              {factor_html}
            </div>
            <div style="flex:1; min-width:110px; text-align:right;">
              <div class="small-label">현재가</div>
              <div style="font-size:17px; font-weight:700; color:#f0f5ff;">
                ₩{price:,}
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
#  Tabs
# ============================================================
tab_rec, tab_sim, tab_real, tab_hist, tab_perf, tab_info = st.tabs(
    ["🎯 오늘의 추천", "🧪 모의투자", "💼 보유중", "📜 거래이력", "📊 성과", "⚙️ 정보"]
)


# === Recommendations ===
with tab_rec:
    f = latest_scores_file()
    if not f:
        st.markdown(
            """<div class="empty-panel">
              <div class="empty-emoji">📭</div>
              <div style="font-size:14px; font-weight:700; color:#e5edff;">
                점수 파일 없음
              </div>
              <div style="margin-top:6px;">
                데스크탑 앱에서 '오늘 점수 계산' 실행 →  <br>
                GitHub에 push 되면 여기에도 반영됩니다.
              </div>
            </div>""",
            unsafe_allow_html=True,
        )
    else:
        try:
            df = pd.read_json(f)
            if "total_score" in df.columns:
                df = df.sort_values("total_score", ascending=False)

            # top summary line
            min_score = CONFIG["portfolio_limits"]["min_score_to_buy"]
            n85 = int((df["total_score"] >= 85).sum())
            n90 = int((df["total_score"] >= 90).sum())
            regime = df["regime"].iloc[0] if "regime" in df.columns else "?"
            as_of = df["as_of"].iloc[0] if "as_of" in df.columns else "?"
            st.markdown(
                f'<div style="margin-bottom:12px; color:#9aa8c7; font-size:12px;">'
                f'<b style="color:#f0f5ff;">{as_of}</b> 기준 · '
                f'시장 국면: <span class="pill pill-blue">{regime}</span> · '
                f'85+ <b style="color:#22c55e">{n85}</b>개 · '
                f'90+ <b style="color:#22c55e">{n90}</b>개'
                f'</div>',
                unsafe_allow_html=True,
            )

            top = df[df["total_score"] >= min_score].head(10) if n85 else df.head(10)

            # ========= Charts (2-column) =========
            chart_col1, chart_col2 = st.columns([1, 1])

            # -- Score distribution histogram --
            with chart_col1:
                st.markdown(
                    '<div class="small-label" style="margin:6px 0 4px;">점수 분포 (전체 유니버스)</div>',
                    unsafe_allow_html=True,
                )
                fig_dist = go.Figure()
                fig_dist.add_trace(go.Histogram(
                    x=df["total_score"],
                    nbinsx=30,
                    marker=dict(color="#3b82f6", line=dict(width=0)),
                    opacity=0.85,
                    name="종목",
                ))
                # threshold lines
                for thr, label, color in [
                    (85, "85 추천", "#22c55e"),
                    (90, "90 강추", "#fbbf24"),
                    (95, "95 최강", "#ef4444"),
                ]:
                    fig_dist.add_vline(
                        x=thr, line=dict(color=color, width=1.5, dash="dash"),
                        annotation_text=label, annotation_position="top",
                        annotation_font_size=10, annotation_font_color=color,
                    )
                fig_dist.update_layout(
                    template="plotly_dark",
                    paper_bgcolor="#111c33", plot_bgcolor="#111c33",
                    font=dict(color="#e5edff", family="Segoe UI", size=11),
                    margin=dict(l=40, r=20, t=30, b=36),
                    height=270,
                    bargap=0.05,
                    showlegend=False,
                    xaxis=dict(title="총점", range=[0, 100],
                               gridcolor="#1b2744", zeroline=False),
                    yaxis=dict(title="종목 수", gridcolor="#1b2744",
                               zeroline=False),
                )
                st.plotly_chart(fig_dist, use_container_width=True, theme=None)

            # -- Top 10 factor stack --
            with chart_col2:
                st.markdown(
                    '<div class="small-label" style="margin:6px 0 4px;">상위 10종목 팩터 구성</div>',
                    unsafe_allow_html=True,
                )
                top10 = df.head(10).copy().iloc[::-1]  # bottom-up for horizontal bars
                fig_stack = go.Figure()
                weights = CONFIG["scoring"]["factors"]
                factor_cols = [
                    ("mean_reversion_score", "역추세", "#fbbf24",
                     weights["mean_reversion"]),
                    ("quality_score",        "퀄리티", "#a855f7",
                     weights["quality"]),
                    ("supply_demand_score",  "수급",   "#3b82f6",
                     weights["supply_demand"]),
                    ("momentum_score",       "모멘텀", "#22c55e",
                     weights["momentum"]),
                ]
                for col, name, color, w in factor_cols:
                    if col not in top10.columns:
                        continue
                    contrib = top10[col] * w / 100.0
                    fig_stack.add_trace(go.Bar(
                        y=top10["name"],
                        x=contrib,
                        orientation="h",
                        name=name,
                        marker=dict(color=color),
                        hovertemplate=(f"{name}: %{{customdata:.1f}}점 × {w}%<br>"
                                       "기여도: %{x:.1f}<extra></extra>"),
                        customdata=top10[col],
                    ))
                fig_stack.update_layout(
                    template="plotly_dark",
                    paper_bgcolor="#111c33", plot_bgcolor="#111c33",
                    font=dict(color="#e5edff", family="Segoe UI", size=11),
                    margin=dict(l=130, r=20, t=30, b=36),
                    height=270,
                    barmode="stack",
                    showlegend=True,
                    legend=dict(orientation="h", yanchor="bottom", y=-0.4,
                                xanchor="center", x=0.5, font=dict(size=10)),
                    xaxis=dict(title="가중 기여 점수", gridcolor="#1b2744",
                               zeroline=False, range=[0, 100]),
                    yaxis=dict(gridcolor="#1b2744", zeroline=False),
                )
                st.plotly_chart(fig_stack, use_container_width=True, theme=None)

            st.write("")  # spacing

            if top.empty:
                st.info("추천 대상이 없습니다.")
            else:
                held_tickers = set(portfolio["positions"].keys())
                for _, row in top.iterrows():
                    render_stock_card(row.to_dict(), held=row["ticker"] in held_tickers)
                st.caption(
                    "💡 매수/매도 버튼은 **데스크탑 앱**에서 사용 ·  "
                    "웹은 읽기 전용 대시보드"
                )
        except Exception:
            st.error("Score load failed")
            st.code(traceback.format_exc())


# === Simulation positions ===
def _render_positions(mode_label: str, mode_key: str):
    pos_list = [p for p in portfolio["positions"].values()
                if p.get("mode", "simulation") == mode_key]
    if not pos_list:
        if mode_key == "simulation":
            st.markdown(
                """<div class="empty-panel">
                  <div class="empty-emoji">🧪</div>
                  <div style="color:#e5edff; font-weight:700;">모의 보유 없음</div>
                  <div style="margin-top:6px;">데스크탑 앱 '오늘의 추천' 탭에서 매수 시작</div>
                </div>""",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                """<div class="empty-panel">
                  <div class="empty-emoji">💼</div>
                  <div style="color:#e5edff; font-weight:700;">실전 계좌 미연동</div>
                  <div style="margin-top:6px;">
                    Phase 5에서 증권사 API 연동 예정<br>
                    조건: 5년 백테스트 통과 + 모의투자 2주 + 알파 +3%
                  </div>
                </div>""",
                unsafe_allow_html=True,
            )
        return

    for p in pos_list:
        ticker = p["ticker"]
        name = p["name"]
        entry = p["entry_price"]
        qty = p["qty"]
        cost = p["cost_krw"]
        entry_date = p["entry_date"]
        score = p.get("entry_score", 0)

        st.markdown(
            f"""
            <div class="stock-card">
              <div style="display:flex; align-items:center; gap:20px;">
                <div style="flex:2; min-width:160px;">
                  <div class="stock-name">{name}</div>
                  <div class="stock-ticker">{ticker} · 진입 {entry_date}</div>
                  <div style="margin-top:8px;">
                    <span class="pill pill-gray">진입점수 {score:.1f}</span>
                  </div>
                </div>
                <div style="flex:1;">
                  <div class="small-label">진입가</div>
                  <div style="font-size:16px; font-weight:700;">
                    ₩{entry:,.0f}
                  </div>
                </div>
                <div style="flex:1;">
                  <div class="small-label">수량</div>
                  <div style="font-size:16px; font-weight:700;">{qty}주</div>
                </div>
                <div style="flex:1;">
                  <div class="small-label">원가</div>
                  <div style="font-size:16px; font-weight:700;">
                    ₩{cost:,.0f}
                  </div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


with tab_sim:
    _render_positions("모의투자", "simulation")

with tab_real:
    _render_positions("보유중", "real")


# === History ===
with tab_hist:
    trades = history.get("trades", [])
    if not trades:
        st.markdown(
            """<div class="empty-panel">
              <div class="empty-emoji">📜</div>
              <div style="color:#e5edff; font-weight:700;">거래 기록 없음</div>
              <div style="margin-top:6px;">매수/매도가 발생하면 여기에 기록됩니다.</div>
            </div>""",
            unsafe_allow_html=True,
        )
    else:
        # ---- Summary strip ----
        buys = [t for t in trades if t.get("action") == "buy"]
        sells = [t for t in trades if t.get("action") == "sell"]
        total_bought = sum(t.get("cost_krw", 0) for t in buys)
        total_sold = sum(t.get("proceeds_krw", 0) for t in sells)
        realized = sum(t.get("pnl_krw", 0) for t in sells)

        h1, h2, h3, h4 = st.columns(4)
        with h1:
            st.markdown(kpi_card("매수 건수", f"{len(buys)}건", "blue",
                                 f"누적 {total_bought:,.0f}원"),
                        unsafe_allow_html=True)
        with h2:
            st.markdown(kpi_card("매도 건수", f"{len(sells)}건", "",
                                 f"누적 {total_sold:,.0f}원"),
                        unsafe_allow_html=True)
        with h3:
            c = "green" if realized >= 0 else "red"
            sign = "+" if realized >= 0 else ""
            st.markdown(kpi_card("실현 손익", f"{sign}{realized:,.0f}원", c, ""),
                        unsafe_allow_html=True)
        with h4:
            if sells:
                wins = sum(1 for t in sells if t.get("pnl_krw", 0) > 0)
                rate = wins / len(sells) * 100
                st.markdown(kpi_card("매도 승률", f"{rate:.1f}%",
                                     "green" if rate >= 50 else "red",
                                     f"{wins}승 {len(sells)-wins}패"),
                            unsafe_allow_html=True)
            else:
                st.markdown(kpi_card("매도 승률", "–", "", ""),
                            unsafe_allow_html=True)

        st.write("")

        # ---- Trade rows (custom HTML, brokerage-style) ----
        # Streamlit's markdown treats 4+ leading spaces as a code block, so
        # build HTML on SINGLE LINES with no leading whitespace.
        def _trade_time(t):
            return t.get("exit_date") or t.get("entry_date") or ""
        trades_sorted = sorted(trades, key=_trade_time, reverse=True)

        GRID_COLS = "110px 90px 1fr 90px 100px 110px 100px 100px 1fr"
        HEAD_STYLE = (
            f"display:grid;grid-template-columns:{GRID_COLS};"
            "padding:11px 16px;background:#0f172a;"
            "border-bottom:1px solid #22324f;"
            "font-size:10.5px;font-weight:700;letter-spacing:0.6px;"
            "color:#9aa8c7;text-transform:uppercase;"
        )
        ROW_STYLE = (
            f"display:grid;grid-template-columns:{GRID_COLS};"
            "padding:12px 16px;border-bottom:1px solid #1b2744;"
            "font-size:12.5px;align-items:center;"
            "font-variant-numeric:tabular-nums;"
        )

        html_parts = [
            '<div style="background:#111c33;border:1px solid #1b2744;border-radius:12px;overflow:hidden;">',
            f'<div style="{HEAD_STYLE}">',
            '<div>날짜</div><div>구분</div><div>종목</div>',
            '<div style="text-align:right;">수량</div>',
            '<div style="text-align:right;">단가</div>',
            '<div style="text-align:right;">금액</div>',
            '<div style="text-align:right;">수익률</div>',
            '<div style="text-align:right;">손익</div>',
            '<div>사유</div></div>',
        ]

        KOREAN_REASONS = {
            "hard_stop_loss": "하드 손절",
            "time_stop": "타임 스톱",
            "take_profit_partial": "부분 익절",
            "trailing_stop": "트레일링 스톱",
            "sell_score_stage2": "매도점수 80+",
            "sell_score_stage1": "매도점수 60+",
            "manual": "수동",
            "momentum_reversal": "모멘텀 반전",
            "foreign_sell": "외국인 순매도",
            "ma5_break": "MA5 이탈",
        }

        for t in trades_sorted:
            action = t.get("action", "")
            date = t.get("exit_date") or t.get("entry_date", "")
            ticker = t.get("ticker", "")
            name = t.get("name", "")
            qty = int(t.get("qty", 0))
            price = float(t.get("price", 0) or 0)
            pnl_pct = t.get("pnl_pct", None)
            pnl_krw = t.get("pnl_krw", None)
            reason = t.get("reason", "") or ""

            if action == "buy":
                total_amt = t.get("cost_krw", qty * price)
                pill_cls, pill_text = "pill-blue", "매수"
                pnl_cell = '<span style="color:#6b7a9c;">–</span>'
                profit_cell = '<span style="color:#6b7a9c;">–</span>'
                reason_display = (
                    f'진입점수 {t.get("entry_score", 0):.1f}'
                    if "entry_score" in t else ""
                )
            else:
                total_amt = t.get("proceeds_krw", qty * price)
                pill_cls = "pill-green" if (pnl_krw or 0) >= 0 else "pill-red"
                pill_text = "매도"
                if pnl_pct is not None:
                    c = "#22c55e" if pnl_pct >= 0 else "#ef4444"
                    s = "+" if pnl_pct >= 0 else ""
                    pnl_cell = f'<span style="color:{c};font-weight:700;">{s}{pnl_pct:.2f}%</span>'
                else:
                    pnl_cell = '–'
                if pnl_krw is not None:
                    c = "#22c55e" if pnl_krw >= 0 else "#ef4444"
                    s = "+" if pnl_krw >= 0 else ""
                    profit_cell = f'<span style="color:{c};font-weight:700;">{s}{pnl_krw:,.0f}원</span>'
                else:
                    profit_cell = '–'
                reason_display = reason or "-"

            for eng, kor in KOREAN_REASONS.items():
                reason_display = reason_display.replace(eng, kor)

            # Build row as single line (no indentation!)
            row = (
                f'<div style="{ROW_STYLE}">'
                f'<div style="color:#9aa8c7;font-family:Consolas,monospace;">{date}</div>'
                f'<div><span class="pill {pill_cls}">{pill_text}</span></div>'
                f'<div>'
                f'<div style="color:#f0f5ff;font-weight:700;">{name}</div>'
                f'<div style="color:#6b7a9c;font-size:10.5px;font-family:Consolas,monospace;">{ticker}</div>'
                f'</div>'
                f'<div style="text-align:right;color:#e5edff;">{qty:,}주</div>'
                f'<div style="text-align:right;color:#e5edff;">₩{price:,.0f}</div>'
                f'<div style="text-align:right;color:#f0f5ff;font-weight:700;">₩{total_amt:,.0f}</div>'
                f'<div style="text-align:right;">{pnl_cell}</div>'
                f'<div style="text-align:right;">{profit_cell}</div>'
                f'<div style="color:#9aa8c7;font-size:11.5px;">{reason_display}</div>'
                f'</div>'
            )
            html_parts.append(row)

        html_parts.append('</div>')
        st.markdown("".join(html_parts), unsafe_allow_html=True)


# === Performance ===
with tab_perf:
    trades = history.get("trades", [])
    sells = [t for t in trades if t.get("action") == "sell" or t.get("action") == "매도"]
    if not sells:
        st.markdown(
            """<div class="empty-panel">
              <div class="empty-emoji">📊</div>
              <div style="color:#e5edff; font-weight:700;">매도 거래 없음</div>
              <div style="margin-top:6px;">첫 매도가 발생하면 누적 손익 차트가 표시됩니다.</div>
            </div>""",
            unsafe_allow_html=True,
        )
    else:
        df = pd.DataFrame(sells)
        df["exit_date"] = pd.to_datetime(df["exit_date"])
        df = df.sort_values("exit_date")
        df["cum_pnl"] = df["pnl_krw"].cumsum()

        # KPI row
        total = df["pnl_krw"].sum()
        wins = (df["pnl_krw"] > 0).sum()
        rate = wins / len(df) * 100
        avg = df["pnl_pct"].mean()
        p1, p2, p3, p4 = st.columns(4)
        with p1: st.markdown(kpi_card("TRADES", f"{len(df)}", ""), unsafe_allow_html=True)
        with p2: st.markdown(kpi_card("WIN RATE", f"{rate:.1f}%",
                                      "green" if rate >= 50 else "red"), unsafe_allow_html=True)
        with p3: st.markdown(kpi_card("AVG RETURN", f"{avg:+.2f}%",
                                      "green" if avg >= 0 else "red"), unsafe_allow_html=True)
        with p4: st.markdown(kpi_card("TOTAL P/L", f"{total:+,.0f}원",
                                      "green" if total >= 0 else "red"), unsafe_allow_html=True)

        st.write("")
        # Plotly curve
        line_color = "#22c55e" if total >= 0 else "#ef4444"
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["exit_date"], y=df["cum_pnl"],
            mode="lines+markers",
            line=dict(color=line_color, width=2.5),
            marker=dict(size=7, color=line_color, line=dict(width=1, color="#fff")),
            name="누적 손익",
            fill="tozeroy",
            fillcolor="rgba(34,197,94,0.08)" if total >= 0 else "rgba(239,68,68,0.08)",
        ))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0b1222",
            plot_bgcolor="#111c33",
            font=dict(color="#e5edff", family="Segoe UI, sans-serif", size=12),
            margin=dict(l=40, r=20, t=20, b=40),
            height=360,
            xaxis=dict(gridcolor="#1b2744", showgrid=True, zeroline=False),
            yaxis=dict(gridcolor="#1b2744", showgrid=True, zeroline=True, zerolinecolor="#22324f",
                       title="누적 손익 (원)"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, theme=None)


# === Info ===
with tab_info:
    st.markdown(
        """
        <div style="display:grid; grid-template-columns: repeat(2, 1fr); gap: 14px;">
        """,
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns(2)
    w = CONFIG["scoring"]["factors"]
    with col1:
        st.markdown(
            f"""
            <div class="stock-card">
              <div class="small-label">SCORING WEIGHTS</div>
              <div style="margin-top:10px; display:grid; grid-template-columns: repeat(2,1fr); gap:10px;">
                <div>모멘텀 <b style="color:#22c55e;">{w['momentum']}%</b></div>
                <div>수급 <b style="color:#3b82f6;">{w['supply_demand']}%</b></div>
                <div>퀄리티 <b style="color:#a855f7;">{w['quality']}%</b></div>
                <div>역추세 <b style="color:#fbbf24;">{w['mean_reversion']}%</b></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        sr = CONFIG["sell_rules"]
        st.markdown(
            f"""
            <div class="stock-card">
              <div class="small-label">SELL RULES</div>
              <div style="margin-top:10px; font-size:12.5px; line-height:1.8;">
                · 하드 손절 <b style="color:#ef4444;">{sr['hard_stop_loss_pct']}%</b><br>
                · 부분 익절 <b style="color:#22c55e;">+{sr['hard_take_profit_partial_pct']}%</b> 에서 50%<br>
                · 타임 스톱 <b>{sr['time_stop_days']}</b> 거래일<br>
                · 트레일링 스톱 <b style="color:#fbbf24;">{sr['trailing_stop_pct']}%</b><br>
                · 매도점수 {sr['sell_score_stage1']}+ → 50% · {sr['sell_score_stage2']}+ → 100%
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    import sys
    st.markdown(
        f"""
        <div style="margin-top:14px; color:#6b7a9c; font-size:11px; text-align:center;">
          Python {sys.version.split()[0]} · Streamlit {st.__version__} ·
          <a href="https://github.com/junkyulee2/stock-advisor" style="color:#3b82f6;">GitHub</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
