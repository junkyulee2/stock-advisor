"""춘큐 스탁 어드바이져 — web dashboard with buy/sell + GitHub sync."""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timedelta
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

# --- PWA-style meta tags: make "Add to Home Screen" look native ---
st.markdown(
    """
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <meta name="apple-mobile-web-app-title" content="춘큐 스탁">
    <meta name="theme-color" content="#FAF9F6">
    <meta name="mobile-web-app-capable" content="yes">
    """,
    unsafe_allow_html=True,
)

# ============================================================
#  Safe imports
# ============================================================
try:
    from src.utils import PROJECT_ROOT, load_config, save_json
    from src import portfolio as pf
    from src import cloud_store
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
CLOUD_MODE = cloud_store.is_configured()


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_current_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    """Last close per ticker. 30-minute cache so repeated reloads don't spam."""
    if not tickers:
        return {}
    import FinanceDataReader as fdr
    today_s = datetime.now().strftime("%Y-%m-%d")
    week_s = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    out: dict[str, float] = {}
    for t in tickers:
        try:
            df = fdr.DataReader(t, week_s, today_s)
            if not df.empty:
                out[t] = float(df["Close"].iloc[-1])
        except Exception:
            pass
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_price_history_df(tickers: tuple[str, ...], days: int = 5) -> pd.DataFrame:
    """Last `days` trading-day closes for each ticker. Columns=tickers, index=date."""
    if not tickers:
        return pd.DataFrame()
    import FinanceDataReader as fdr
    today_s = datetime.now().strftime("%Y-%m-%d")
    start_s = (datetime.now() - timedelta(days=days * 3 + 5)).strftime("%Y-%m-%d")
    series: dict[str, pd.Series] = {}
    for t in tickers:
        try:
            df = fdr.DataReader(t, start_s, today_s)
            if not df.empty:
                series[t] = df["Close"]
        except Exception:
            pass
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).dropna(how="all").tail(days)


def portfolio_pl_series(portfolio: dict, hist: pd.DataFrame) -> list[tuple[str, float]]:
    """Daily total unrealized P/L (KRW) for current holdings over hist rows."""
    if hist.empty or not portfolio.get("positions"):
        return []
    result: list[tuple[str, float]] = []
    positions = portfolio["positions"]
    for idx, row in hist.iterrows():
        pl = 0.0
        for tk, close in row.items():
            if pd.isna(close) or tk not in positions:
                continue
            p = positions[tk]
            pl += (float(close) - p["entry_price"]) * p["qty"]
        result.append((idx.strftime("%m-%d"), pl))
    return result


# ============================================================
#  Cloud-aware data I/O
# ============================================================
@st.cache_data(ttl=20, show_spinner=False)
def _cloud_read(path: str):
    """Returns (data, sha). Uses GitHub API if configured, else local file."""
    if CLOUD_MODE:
        try:
            return cloud_store.read_json(path)
        except Exception as e:
            st.warning(f"cloud read fail ({path}): {e}")
            return None, None
    # Local fallback
    local = PROJECT_ROOT / path
    if not local.exists():
        return None, None
    with open(local, "r", encoding="utf-8") as f:
        return json.load(f), None


def _cloud_write(path: str, data: dict, sha: str | None, message: str) -> bool:
    if CLOUD_MODE:
        try:
            cloud_store.write_json(path, data, sha, message)
            _cloud_read.clear()
            return True
        except Exception as e:
            st.error(f"cloud write failed: {e}")
            st.code(traceback.format_exc())
            return False
    # Local fallback
    local = PROJECT_ROOT / path
    local.parent.mkdir(parents=True, exist_ok=True)
    save_json(local, data)
    _cloud_read.clear()
    return True


def load_portfolio_sha():
    data, sha = _cloud_read("data/portfolio.json")
    if data is None:
        data = pf.empty_portfolio()
    return data, sha


def load_history_sha():
    data, sha = _cloud_read("data/history.json")
    if data is None:
        data = pf.empty_history()
    return data, sha


# ============================================================
#  CSS (same as before)
# ============================================================
def inject_css():
    st.markdown(
        """
        <style>
        /* ======= PASTEL LIGHT THEME (Design 1: B+E mix) ======= */
        .stApp { background:#FAF9F6; color:#1a1a1a; }
        .block-container { max-width:1200px; padding-top:1rem !important; padding-bottom:2rem !important; }
        #MainMenu, footer, header { visibility:hidden; }
        .stDeployButton { display:none; }

        /* ---- Top bar: logo + streak ---- */
        .topbar { display:flex; justify-content:space-between; align-items:center;
                  margin-bottom:12px; }
        .brand { font-size:18px; font-weight:800; color:#1a1a1a; letter-spacing:-0.3px; }
        .streak-pill { background:#FFE4A3; padding:6px 12px; border-radius:999px;
                       font-size:11.5px; font-weight:800; color:#7A4F00;
                       box-shadow:0 2px 0 #E8C876; }

        /* ---- Hero (mint + P/L + chart) ---- */
        .hero { background:#C9F0D2; border-radius:22px;
                padding:18px 20px 10px; margin-bottom:10px;
                box-shadow:0 4px 0 #A8D9B3; }
        .hero-label { font-size:11px; font-weight:800; color:#0F7B0F;
                      letter-spacing:1px; }
        .hero-big { font-size:38px; font-weight:900; color:#0F7B0F;
                    margin-top:4px; letter-spacing:-1.5px; line-height:1; }
        .hero-big.down { color:#CC2222; }
        .hero-pct { font-size:14px; font-weight:800; color:#0F7B0F; margin-top:4px; }
        .hero-pct.down { color:#CC2222; }
        .hero-hint { font-size:11px; font-weight:700; color:rgba(15,123,15,0.7);
                     margin-top:4px; }
        .hero-hint.down { color:rgba(204,34,34,0.75); }
        .hero-chart { height:72px; margin:6px -6px 0; }
        .hero-chart svg { width:100%; height:100%; overflow:visible; }
        .hero-xlabels { display:flex; justify-content:space-between;
                        font-size:9px; font-weight:700; color:#0F7B0F;
                        opacity:0.55; padding:0 2px; }
        .hero-xlabels.down { color:#CC2222; }

        /* ---- KPI mini cards (peach / lavender) ---- */
        .kpi { border-radius:18px; padding:14px 16px; height:100%;
               box-shadow:0 3px 0 rgba(0,0,0,0.08); }
        .kpi.peach { background:#FFD9C0; }
        .kpi.peach .kpi-value { color:#A84F00; }
        .kpi.lav { background:#DDD3F5; }
        .kpi.lav .kpi-value { color:#4C2F9E; }
        .kpi.blueish { background:#CFE3FF; }
        .kpi.blueish .kpi-value { color:#1E4CA3; }
        .kpi.mint { background:#C9F0D2; }
        .kpi.mint .kpi-value { color:#0F7B0F; }
        .kpi-label { font-size:10px; font-weight:800; letter-spacing:1px;
                     opacity:0.7; text-transform:uppercase; }
        .kpi-value { font-size:22px; font-weight:900; margin-top:4px;
                     letter-spacing:-0.5px; font-variant-numeric:tabular-nums; }
        .kpi-hint { font-size:10px; opacity:0.65; margin-top:3px; font-weight:700; }
        .kpi-green { color:#0F7B0F !important; }
        .kpi-red { color:#CC2222 !important; }
        .kpi-gold { color:#B45309 !important; }
        .kpi-blue { color:#1E4CA3 !important; }

        /* ---- Badges row ---- */
        .badges-row { display:flex; gap:8px; margin:4px 0 14px; }
        .badge { flex:1; background:#fff; border-radius:14px;
                 padding:10px 6px; text-align:center;
                 box-shadow:0 2px 0 #E4E4E4; }
        .badge .ic { font-size:20px; line-height:1; }
        .badge .t { font-size:10px; font-weight:800; color:#555; margin-top:4px; }
        .badge.lock { opacity:0.35; }

        /* ---- Stock cards (white on cream) ---- */
        .stock-card { background:#fff; border-radius:18px;
                      padding:16px 18px; margin-bottom:10px;
                      box-shadow:0 2px 0 rgba(0,0,0,0.04),
                                 0 0 0 1px rgba(0,0,0,0.04);
                      transition:transform .15s, box-shadow .15s; }
        .stock-card:hover { transform:translateY(-1px);
                            box-shadow:0 4px 0 rgba(49,130,246,0.12),
                                       0 0 0 1.5px rgba(49,130,246,0.25); }
        .stock-card.held { box-shadow:0 2px 0 rgba(49,130,246,0.15),
                                      0 0 0 1.5px rgba(49,130,246,0.4); }
        .stock-name { font-size:15px; font-weight:800; color:#1a1a1a; letter-spacing:-0.2px; }
        .stock-ticker { font-size:11px; color:#8a8a8a; font-weight:600; margin-top:2px;
                        font-variant-numeric:tabular-nums; }
        .big-score { font-size:28px; font-weight:900; color:#EAB308;
                     font-variant-numeric:tabular-nums; line-height:1; letter-spacing:-0.8px; }
        .small-label { font-size:9.5px; color:#8a8a8a; text-transform:uppercase;
                       letter-spacing:1px; font-weight:800; }

        /* ---- Factor bars ---- */
        .factor-row { display:flex; align-items:center; gap:8px; margin:3px 0; font-size:11px; }
        .factor-name { width:44px; color:#8a8a8a; font-weight:600; }
        .factor-bar { flex:1; height:4px; background:#F0F0F0; border-radius:2px; overflow:hidden; }
        .factor-fill { height:100%; border-radius:2px; }
        .factor-val { width:26px; text-align:right; color:#1a1a1a;
                      font-weight:700; font-variant-numeric:tabular-nums; }

        /* ---- Pills ---- */
        .pill { display:inline-block; padding:3px 10px; border-radius:999px;
                font-size:10.5px; font-weight:800; letter-spacing:0.2px; }
        .pill-green { background:rgba(15,123,15,0.12); color:#0F7B0F;
                      border:1px solid rgba(15,123,15,0.25); }
        .pill-red { background:rgba(204,34,34,0.1); color:#CC2222;
                    border:1px solid rgba(204,34,34,0.25); }
        .pill-blue { background:rgba(49,130,246,0.1); color:#3182F6;
                     border:1px solid rgba(49,130,246,0.3); }
        .pill-gray { background:#F0F0F0; color:#6b6b6b;
                     border:1px solid #E0E0E0; }
        .pill-gold { background:rgba(234,179,8,0.12); color:#B45309;
                     border:1px solid rgba(234,179,8,0.3); }

        /* ---- Tabs ---- */
        .stTabs [data-baseweb="tab-list"] { gap:4px; background:transparent;
                                            border-bottom:1px solid #E5E5E5; }
        .stTabs [data-baseweb="tab"] { background:transparent; color:#8a8a8a;
                                       font-weight:700; padding:10px 16px;
                                       border-radius:10px 10px 0 0; font-size:13px; }
        .stTabs [aria-selected="true"] { background:#fff; color:#1a1a1a;
                                          border:1px solid #E5E5E5;
                                          border-bottom-color:#fff; }

        /* ---- Empty panels ---- */
        .empty-panel { background:#fff; border:1px dashed #D0D0D0;
                       border-radius:18px; padding:32px 20px;
                       text-align:center; color:#6b6b6b;
                       box-shadow:0 2px 0 rgba(0,0,0,0.03); }
        .empty-emoji { font-size:34px; margin-bottom:8px; }

        /* ---- Buttons (3D lifted style) ---- */
        div[data-testid="stButton"] > button {
            background:#fff; color:#1a1a1a;
            border:1px solid #E0E0E0; border-radius:12px;
            font-weight:800; padding:8px 16px;
            box-shadow:0 2px 0 #E4E4E4;
            transition:transform .1s, box-shadow .1s;
        }
        div[data-testid="stButton"] > button:hover {
            border-color:#3182F6; color:#3182F6;
        }
        div[data-testid="stButton"] > button:active {
            transform:translateY(2px);
            box-shadow:0 0 0 #E4E4E4;
        }
        div[data-testid="stFormSubmitButton"] > button {
            background:#3182F6; color:#fff; border:none;
            border-radius:12px; font-weight:800;
            box-shadow:0 3px 0 #1A6AE0;
        }
        div[data-testid="stFormSubmitButton"] > button:hover { background:#2672E8; }
        div[data-testid="stFormSubmitButton"] > button:active {
            transform:translateY(2px); box-shadow:0 0 0 #1A6AE0;
        }

        /* Buy/sell buttons are .primary-action via class attr fallback —
           keep our st.button consistent. Buy expander button inside form: */
        .stock-card + div div[data-testid="stButton"] > button {
            background:#0F7B0F; color:#fff; border:none;
            box-shadow:0 3px 0 #0A5A0A;
        }

        /* ---- Expander ---- */
        details[data-testid="stExpander"] {
            background:#fff; border-radius:14px; border:1px solid #EEE !important;
            box-shadow:0 2px 0 rgba(0,0,0,0.03);
            margin-bottom:10px;
        }
        details[data-testid="stExpander"] summary {
            font-weight:700; color:#1a1a1a;
        }

        /* ---- Info/warning boxes ---- */
        div[data-testid="stAlert"] {
            background:#FFF8E4; border:1px solid #F0D590;
            border-radius:14px; color:#7A4F00;
        }

        /* ---- Overflow safety ---- */
        .kpi-value { overflow-wrap:anywhere; word-break:keep-all; }
        .stock-card { overflow:hidden; }
        .stock-card > div { min-width:0; }
        .table-scroll { overflow-x:auto; -webkit-overflow-scrolling:touch; }

        /* ============ MOBILE (<= 768px) ============ */
        @media (max-width: 768px) {
            .block-container { padding-left:0.7rem !important; padding-right:0.7rem !important; }
            .hero { padding:16px 16px 8px; }
            .hero-big { font-size:32px; }
            .kpi { padding:12px; }
            .kpi-label { font-size:9px; letter-spacing:0.5px; }
            .kpi-value { font-size:18px; }
            .stock-card { padding:14px; }
            .stock-card > div { flex-wrap:wrap !important; gap:10px !important; }
            .stock-name { font-size:14px; }
            .big-score { font-size:22px; }
            .factor-row { font-size:10.5px; }
            .pill { font-size:9.5px; padding:2px 8px; }
            .stTabs [data-baseweb="tab"] { padding:8px 10px; font-size:11px; }
            .badge .ic { font-size:18px; }
            .badge .t { font-size:9px; }
        }
        @media (max-width: 480px) {
            .hero-big { font-size:28px; }
            .kpi-value { font-size:16px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_css()


# ============================================================
#  Load data (BEFORE hero — hero needs P/L numbers)
# ============================================================
portfolio, portfolio_sha = load_portfolio_sha()
history, history_sha = load_history_sha()
_open_tickers = tuple(sorted(portfolio["positions"].keys()))
current_prices = fetch_current_prices(_open_tickers)
summary = pf.compute_summary(portfolio, history, current_prices=current_prices)

realized = summary["realized_pnl_krw"]
unrealized = summary["unrealized_pnl_krw"]
open_cost = summary["open_cost_krw"]
unreal_pct = (unrealized / open_cost * 100) if open_cost > 0 else 0.0
win_rate = summary["win_rate"] * 100


# ---- Streak: days since first trade ----
_streak_days = 0
if history.get("trades"):
    try:
        _first = min(
            t.get("entry_date") or t.get("exit_date", "9999-99-99")
            for t in history["trades"]
        )
        _d0 = datetime.strptime(_first, "%Y-%m-%d")
        _streak_days = max(1, (datetime.now() - _d0).days + 1)
    except Exception:
        _streak_days = 0


# ============================================================
#  Top bar (brand + streak)
# ============================================================
_streak_html = (f'<div class="streak-pill">🔥 {_streak_days}일차</div>'
                if _streak_days > 0 else '')
st.markdown(
    f'<div class="topbar"><div class="brand">📈 춘큐 스탁 어드바이져</div>'
    f'{_streak_html}</div>',
    unsafe_allow_html=True,
)


# ============================================================
#  Hero — 평가손익 + 5일 P/L 그래프
# ============================================================
def _render_hero_chart(points: list[tuple[str, float]], up: bool) -> str:
    """Render inline SVG line chart for 5-day P/L history."""
    if not points:
        # Placeholder baseline when no history yet
        return (
            '<div class="hero-chart"><svg viewBox="0 0 300 72" preserveAspectRatio="none">'
            '<line x1="0" y1="36" x2="300" y2="36" stroke="#0F7B0F" '
            'stroke-opacity="0.3" stroke-dasharray="3 4" stroke-width="1.5"/>'
            '</svg></div>'
            '<div class="hero-xlabels"><span>데이터 쌓이는 중…</span></div>'
        )

    xs = [i for i, _ in enumerate(points)]
    ys = [v for _, v in points]
    lo, hi = min(ys), max(ys)
    span = hi - lo if hi != lo else max(abs(hi), 1)
    pad = span * 0.15 or 1
    lo_p, hi_p = lo - pad, hi + pad
    w, h = 300, 72
    n = max(1, len(points) - 1)

    def _xy(i: int, y: float) -> tuple[float, float]:
        x = (i / n) * w if n > 0 else w / 2
        ny = (y - lo_p) / (hi_p - lo_p) if hi_p != lo_p else 0.5
        yy = h - ny * h * 0.85 - h * 0.075
        return x, yy

    path_d = ""
    for i, (_, v) in enumerate(points):
        x, y = _xy(i, v)
        path_d += ("M" if i == 0 else " L") + f"{x:.1f},{y:.1f}"
    fill_d = path_d + f" L{w},{h} L0,{h} Z"

    color = "#0F7B0F" if up else "#CC2222"
    lx, ly = _xy(len(points) - 1, ys[-1])
    xlabels_cls = "" if up else " down"
    label_spans = "".join(f"<span>{d}</span>" for d, _ in points)

    return (
        f'<div class="hero-chart"><svg viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
        f'<defs><linearGradient id="heroG" x1="0" x2="0" y1="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.32"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/>'
        f'</linearGradient></defs>'
        f'<path d="{fill_d}" fill="url(#heroG)"/>'
        f'<path d="{path_d}" stroke="{color}" stroke-width="2.5" '
        f'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="4" fill="{color}"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="7" fill="{color}" opacity="0.25"/>'
        f'</svg></div>'
        f'<div class="hero-xlabels{xlabels_cls}">{label_spans}</div>'
    )


_pl_hist_df = fetch_price_history_df(_open_tickers, days=5)
_pl_points = portfolio_pl_series(portfolio, _pl_hist_df)

if current_prices:
    _up = unrealized >= 0
    _down_cls = "" if _up else " down"
    _sign = "+" if _up else ""
    _hint = f"{_sign}{unreal_pct:.2f}% · 30분 캐시"
    st.markdown(
        f'<div class="hero">'
        f'<div class="hero-label">평가손익</div>'
        f'<div class="hero-big{_down_cls}">{_sign}{unrealized:,.0f}원</div>'
        f'<div class="hero-pct{_down_cls}">{_hint}</div>'
        f'{_render_hero_chart(_pl_points, _up)}'
        f'</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="hero">'
        '<div class="hero-label">평가손익</div>'
        '<div class="hero-big" style="color:#6b6b6b;">보유 없음</div>'
        '<div class="hero-hint">추천 탭에서 첫 매수를 시작하세요</div>'
        '</div>',
        unsafe_allow_html=True,
    )


if not CLOUD_MODE:
    st.info(
        "🔑 **GITHUB_TOKEN이 설정 안됨** — 웹에서 매수/매도 불가. "
        "Streamlit Cloud → Settings → Secrets에 `GITHUB_TOKEN` 추가 시 자동으로 클라우드 모드 활성화."
    )


# ============================================================
#  KPI mini-cards (peach / lavender / blueish / mint)
# ============================================================
def kpi_card(label, value, color_cls="peach", hint=""):
    """color_cls: 'peach' | 'lav' | 'blueish' | 'mint'"""
    return (f'<div class="kpi {color_cls}"><div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div>'
            f'<div class="kpi-hint">{hint}</div></div>')


k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown(kpi_card("보유 종목", f"{summary['open_positions']}",
                         "peach", "현재 보유 수"),
                unsafe_allow_html=True)
with k2:
    if summary["closed_trades"] > 0:
        st.markdown(kpi_card("승률", f"{win_rate:.1f}%", "lav",
                             f"{summary['wins']}승 / {summary['losses']}패"),
                    unsafe_allow_html=True)
    else:
        st.markdown(kpi_card("승률", "–", "lav", "매도 기록 없음"),
                    unsafe_allow_html=True)
with k3:
    _rs = "+" if realized >= 0 else ""
    st.markdown(kpi_card("실현 손익", f"{_rs}{realized:,.0f}원",
                         "blueish", "누적 실현"),
                unsafe_allow_html=True)
with k4:
    st.markdown(kpi_card("총 거래", f"{summary['trades_count']}",
                         "mint", "매수+매도 전체"),
                unsafe_allow_html=True)


# ============================================================
#  Achievement badges
# ============================================================
_b_first = len([t for t in history.get("trades", []) if t.get("action") == "buy"]) > 0
_b_streak3 = _streak_days >= 3
_b_plus = unrealized > 0 if current_prices else False
_b_win50 = win_rate >= 50 and summary["closed_trades"] >= 4

def _badge(icon: str, text: str, on: bool) -> str:
    cls = "" if on else " lock"
    return f'<div class="badge{cls}"><div class="ic">{icon if on else "🔒"}</div><div class="t">{text}</div></div>'

st.markdown(
    f'<div class="badges-row">'
    f'{_badge("🏆", "첫 매수", _b_first)}'
    f'{_badge("🎯", "3일연속", _b_streak3)}'
    f'{_badge("💪", "플러스", _b_plus)}'
    f'{_badge("⭐", "승률 50+", _b_win50)}'
    f'</div>',
    unsafe_allow_html=True,
)


# ============================================================
#  Helpers
# ============================================================
def latest_scores_file() -> Path | None:
    if not SCORES_DIR.exists():
        return None
    files = sorted(SCORES_DIR.glob("scores_*.json"))
    return files[-1] if files else None


def prev_scores_file() -> Path | None:
    """Second-most-recent scores file, for day-over-day comparison."""
    if not SCORES_DIR.exists():
        return None
    files = sorted(SCORES_DIR.glob("scores_*.json"))
    return files[-2] if len(files) >= 2 else None


def _rank_badge(ticker: str, today_rank: int, prev_rank_map: dict) -> str:
    prev = prev_rank_map.get(ticker)
    if prev is None:
        return '<span class="pill pill-gold" style="font-size:9.5px;">🆕 NEW</span>'
    delta = prev - today_rank
    if delta > 0:
        return f'<span class="pill pill-green" style="font-size:9.5px;">↑{delta}</span>'
    if delta < 0:
        return f'<span class="pill pill-red" style="font-size:9.5px;">↓{-delta}</span>'
    return '<span class="pill pill-gray" style="font-size:9.5px;">→</span>'


FACTOR_META = [
    ("모멘텀", "momentum_score", "#22c55e"),
    ("수급",   "supply_demand_score", "#3b82f6"),
    ("퀄리티", "quality_score", "#a855f7"),
    ("역추세", "mean_reversion_score", "#fbbf24"),
]


def render_stock_card(rec: dict, held: bool = False, rank_badge: str = ""):
    name = rec.get("name", "-")
    ticker = rec.get("ticker", "")
    score = float(rec.get("total_score", 0))
    price = int(rec.get("close", 0))
    amount = int(rec.get("amount_krw", 0))
    market = rec.get("market", "")

    factor_html = ""
    for fname, key, color in FACTOR_META:
        v = float(rec.get(key, 0) or 0)
        factor_html += (f'<div class="factor-row">'
                        f'<span class="factor-name">{fname}</span>'
                        f'<div class="factor-bar"><div class="factor-fill" '
                        f'style="width:{max(0,min(100,v))}%;background:{color};"></div></div>'
                        f'<span class="factor-val">{v:.0f}</span></div>')

    if held:
        pill = '<span class="pill pill-blue">● 보유중</span>'
    elif amount > 0:
        pill = f'<span class="pill pill-green">배정 {amount:,}원</span>'
    else:
        pill = '<span class="pill pill-gray">85점 미달</span>'
    if rank_badge:
        pill = f'{rank_badge} {pill}'

    st.markdown(
        f'<div class="stock-card">'
        f'<div style="display:flex;align-items:center;gap:24px;">'
        f'<div style="flex:2;min-width:160px;">'
        f'<div class="stock-name">{name}</div>'
        f'<div class="stock-ticker">{ticker} · {market}</div>'
        f'<div style="margin-top:8px;">{pill}</div>'
        f'</div>'
        f'<div style="flex:0 0 90px;text-align:center;">'
        f'<div class="small-label">SCORE</div>'
        f'<div class="big-score">{score:.1f}</div>'
        f'</div>'
        f'<div style="flex:3;min-width:220px;">{factor_html}</div>'
        f'<div style="flex:1;min-width:110px;text-align:right;">'
        f'<div class="small-label">현재가</div>'
        f'<div style="font-size:17px;font-weight:700;color:#1a1a1a;">₩{price:,}</div>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )


def web_buy(rec: dict, amount_krw: int) -> bool:
    """Execute a buy in cloud/local store. Returns True on success."""
    price = float(rec.get("close", 0) or 0)
    if price <= 0:
        st.error("가격이 0입니다")
        return False

    # Always reload latest before modifying (avoid stale sha)
    portfolio_latest, sha = load_portfolio_sha()
    history_latest, hsha = load_history_sha()

    ticker = rec["ticker"]
    if ticker in portfolio_latest["positions"]:
        st.warning("이미 보유 중입니다")
        return False

    try:
        pos = pf.buy(portfolio_latest, ticker=ticker, name=rec["name"],
                     price=price, amount_krw=amount_krw,
                     score=float(rec["total_score"]))
        pos["mode"] = "simulation"
        pf.record_buy_history(history_latest, pos)
    except Exception as e:
        st.error(f"매수 계산 실패: {e}")
        return False

    ok1 = _cloud_write("data/portfolio.json", portfolio_latest, sha,
                       f"web: buy {ticker} @ {price:,.0f}")
    ok2 = _cloud_write("data/history.json", history_latest, hsha,
                       f"web: history buy {ticker}")
    return ok1 and ok2


def web_add(ticker: str, price: float, amount_krw: int, score: float) -> bool:
    """Pyramid-add to existing position via cloud/local store."""
    if price <= 0:
        st.error("가격이 0입니다")
        return False
    portfolio_latest, sha = load_portfolio_sha()
    history_latest, hsha = load_history_sha()

    if ticker not in portfolio_latest["positions"]:
        st.error("보유 종목이 아닙니다")
        return False

    limits = CONFIG.get("portfolio_limits", {})
    max_adds = int(limits.get("max_adds_per_position", 3))
    existing_adds = portfolio_latest["positions"][ticker].get("add_count", 0)
    if existing_adds >= max_adds:
        st.warning(f"추가 매수 한도 초과 (최대 {max_adds}회)")
        return False

    try:
        pos, new_qty = pf.add_to_position(
            portfolio_latest, ticker=ticker, price=price,
            amount_krw=amount_krw, score=score,
        )
        pf.record_add_history(history_latest,
                              ticker=ticker, name=pos["name"],
                              qty=new_qty, price=price, score=score)
    except Exception as e:
        st.error(f"추가 매수 계산 실패: {e}")
        return False

    ok1 = _cloud_write("data/portfolio.json", portfolio_latest, sha,
                       f"web: add {ticker} +{new_qty}주 @ {price:,.0f}")
    ok2 = _cloud_write("data/history.json", history_latest, hsha,
                       f"web: history add {ticker}")
    if ok1 and ok2:
        st.success(f"추가 매수: {pos['name']} +{new_qty}주 @ ₩{price:,.0f}")
    return ok1 and ok2


def web_sell(ticker: str, current_price: float, sell_ratio: float, reason: str) -> bool:
    portfolio_latest, sha = load_portfolio_sha()
    history_latest, hsha = load_history_sha()

    if ticker not in portfolio_latest["positions"]:
        st.error("보유 종목이 아닙니다")
        return False
    try:
        trade = pf.sell(portfolio_latest, history_latest,
                        ticker=ticker, price=current_price,
                        sell_ratio=sell_ratio, reason=reason)
    except Exception as e:
        st.error(f"매도 계산 실패: {e}")
        return False

    ok1 = _cloud_write("data/portfolio.json", portfolio_latest, sha,
                       f"web: sell {ticker} {int(sell_ratio*100)}%")
    ok2 = _cloud_write("data/history.json", history_latest, hsha,
                       f"web: history sell {ticker}")
    if ok1 and ok2:
        st.success(f"매도 완료: {trade['name']} "
                   f"{trade['pnl_krw']:+,.0f}원 ({trade['pnl_pct']:+.2f}%)")
    return ok1 and ok2


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
            '<div class="empty-panel"><div class="empty-emoji">📭</div>'
            '<div style="font-size:14px;font-weight:700;color:#1a1a1a;">점수 파일 없음</div>'
            '<div style="margin-top:6px;">GitHub Actions가 매일 저녁 자동 계산합니다.<br>'
            '(또는 데스크탑 앱에서 "오늘 점수 계산" 실행)</div></div>',
            unsafe_allow_html=True,
        )
    else:
        try:
            df = pd.read_json(f)
            if "total_score" in df.columns:
                df = df.sort_values("total_score", ascending=False).reset_index(drop=True)

            # Previous day's scores for day-over-day rank/score comparison
            prev_rank_map: dict[str, int] = {}
            prev_score_map: dict[str, float] = {}
            pf_ = prev_scores_file()
            if pf_ is not None:
                try:
                    prev_df = pd.read_json(pf_).sort_values(
                        "total_score", ascending=False
                    ).reset_index(drop=True)
                    prev_rank_map = {r["ticker"]: i + 1 for i, r in prev_df.iterrows()}
                    prev_score_map = {r["ticker"]: float(r["total_score"])
                                      for _, r in prev_df.iterrows()}
                except Exception:
                    pass

            min_score = CONFIG["portfolio_limits"]["min_score_to_buy"]
            n85 = int((df["total_score"] >= 85).sum())
            n90 = int((df["total_score"] >= 90).sum())
            regime = df["regime"].iloc[0] if "regime" in df.columns else "?"
            as_of = df["as_of"].iloc[0] if "as_of" in df.columns else "?"
            st.markdown(
                f'<div style="margin-bottom:12px;color:#8a8a8a;font-size:12px;">'
                f'<b style="color:#1a1a1a;">{as_of}</b> · '
                f'국면 <span class="pill pill-blue">{regime}</span> · '
                f'85+ <b style="color:#0F7B0F">{n85}</b> · '
                f'90+ <b style="color:#0F7B0F">{n90}</b></div>',
                unsafe_allow_html=True,
            )

            held_tickers = set(portfolio["positions"].keys())

            # ----- Refresh scores via GitHub Actions workflow_dispatch -----
            if CLOUD_MODE:
                rc1, rc2 = st.columns([3, 2])
                with rc1:
                    if st.button("🔄 최신 점수 갱신 (GitHub Actions)",
                                 key="refresh_scores",
                                 use_container_width=True,
                                 help="GitHub Actions를 수동 실행해 최신 점수 재계산. "
                                      "완료까지 5~10분 소요 · 완료 후 페이지 새로고침"):
                        trig = getattr(cloud_store, "trigger_workflow", None)
                        if trig is None:
                            st.error("갱신 함수 누락 — 배포 캐시 문제. 잠시 후 재시도.")
                        else:
                            try:
                                trig()
                                st.success("✅ 점수 재계산 요청됨. 5~10분 후 새로고침.")
                                st.toast("GitHub Actions 실행 중...", icon="🔄")
                            except Exception as e:
                                st.error(f"갱신 실패: {e}")
                with rc2:
                    run_fn = getattr(cloud_store, "last_workflow_run", None)
                    run_info = None
                    if run_fn is not None:
                        try:
                            run_info = run_fn()
                        except Exception:
                            run_info = None
                    if run_info:
                        status = run_info.get("status", "?")
                        conclusion = run_info.get("conclusion") or ""
                        if status == "completed":
                            emoji = "✅" if conclusion == "success" else "❌"
                            label = f"{emoji} {conclusion}"
                        elif status in ("queued", "in_progress"):
                            label = f"⏳ {status}"
                        else:
                            label = status
                        st.caption(f"마지막 실행: {label}")

            # ==================================================
            #  SECTION 1: 새로운 추천 (보유 종목 제외, 80점+ 상위 5)
            # ==================================================
            st.markdown(
                '<div style="font-size:16px;font-weight:800;color:#1a1a1a;'
                'margin:10px 0 10px;">🎯 새로운 추천</div>',
                unsafe_allow_html=True,
            )

            fresh_df = df[(df["total_score"] >= min_score)
                          & (~df["ticker"].isin(held_tickers))].head(5)

            if fresh_df.empty:
                st.markdown(
                    '<div class="empty-panel"><div class="empty-emoji">🛡️</div>'
                    '<div style="color:#1a1a1a;font-weight:700;">오늘은 새로 살 거 없음</div>'
                    '<div style="margin-top:6px;">85점 이상 신규 종목 없음 · '
                    '보유 종목 유지가 오늘의 답입니다.</div></div>',
                    unsafe_allow_html=True,
                )
            else:
                for idx, row in fresh_df.iterrows():
                    rec = row.to_dict()
                    ticker = rec["ticker"]
                    today_rank = int(idx) + 1  # idx is preserved from reset_index
                    badge = _rank_badge(ticker, today_rank, prev_rank_map)
                    render_stock_card(rec, held=False, rank_badge=badge)

                    # Buy form
                    if CLOUD_MODE and rec.get("amount_krw", 0) > 0:
                        with st.expander(f"💰 {rec['name']} 매수하기", expanded=False):
                            price = int(rec.get("close", 0))
                            auto_amt = int(rec.get("amount_krw", 0))
                            c1, c2, c3 = st.columns([2, 2, 1])
                            with c1:
                                options = [100_000, 200_000, 300_000]
                                labels = [f"{a:,}원" +
                                          (" (자동)" if a == auto_amt else "")
                                          for a in options]
                                choice = st.radio("금액 선택", labels,
                                                  index=options.index(auto_amt)
                                                  if auto_amt in options else 0,
                                                  horizontal=True,
                                                  key=f"radio_{ticker}")
                                chosen_amt = options[labels.index(choice)]
                            with c2:
                                qty = max(1, chosen_amt // price)
                                cost = qty * price
                                over = cost > chosen_amt
                                cost_color = "#EAB308" if over else "#0F7B0F"
                                over_txt = " (배정 초과)" if over else ""
                                st.markdown(
                                    f"<div style='margin-top:12px;font-size:13px;'>"
                                    f"<b>{qty}주</b> × ₩{price:,} = "
                                    f"<b style='color:{cost_color}'>₩{cost:,}</b>"
                                    f"{over_txt}</div>",
                                    unsafe_allow_html=True,
                                )
                            with c3:
                                if st.button("매수 실행", key=f"buy_{ticker}",
                                             use_container_width=True):
                                    if web_buy(rec, chosen_amt):
                                        st.success(f"{rec['name']} {qty}주 매수 완료")
                                        st.balloons()
                                        st.rerun()
                    elif not CLOUD_MODE:
                        st.caption(f"💡 {rec['name']} 매수는 GITHUB_TOKEN 설정 후 가능")

            # ==================================================
            #  SECTION 2: 내 보유 종목 오늘 상태
            # ==================================================
            if held_tickers:
                st.markdown(
                    '<div style="font-size:16px;font-weight:800;color:#1a1a1a;'
                    'margin:22px 0 10px;">💼 내 보유 종목 오늘 상태</div>',
                    unsafe_allow_html=True,
                )
                today_map = {row["ticker"]: row.to_dict() for _, row in df.iterrows()}
                for ticker in held_tickers:
                    pos = portfolio["positions"][ticker]
                    rec_today = today_map.get(ticker)
                    cur_price = current_prices.get(ticker)
                    prev_s = prev_score_map.get(ticker)

                    # Today's score + day delta
                    if rec_today is not None:
                        cur_s = float(rec_today.get("total_score", 0))
                        if prev_s is not None:
                            d = cur_s - prev_s
                            if abs(d) < 0.05:
                                delta_html = '<span style="color:#6b6b6b;font-size:11px;">→</span>'
                            elif d > 0:
                                delta_html = f'<span style="color:#0F7B0F;font-size:11px;font-weight:700;">↑{d:.1f}</span>'
                            else:
                                delta_html = f'<span style="color:#CC2222;font-size:11px;font-weight:700;">↓{-d:.1f}</span>'
                        else:
                            delta_html = ""
                        score_html = (f'<div style="font-size:22px;font-weight:900;'
                                      f'color:#EAB308;line-height:1;">{cur_s:.1f}</div>'
                                      f'<div style="margin-top:3px;">{delta_html}</div>')
                    else:
                        score_html = ('<div style="color:#6b6b6b;font-size:11px;'
                                      'line-height:1.4;">Top 500<br/>밖</div>')

                    # Current price + P/L
                    entry = pos["entry_price"]
                    qty = pos["qty"]
                    if cur_price:
                        pl = (cur_price - entry) * qty
                        pl_pct = (cur_price / entry - 1) * 100
                        c = "#0F7B0F" if pl >= 0 else "#CC2222"
                        s = "+" if pl >= 0 else ""
                        pl_html = (
                            f'<div style="font-size:14px;font-weight:700;color:#1a1a1a;">'
                            f'₩{cur_price:,.0f}</div>'
                            f'<div style="font-size:13px;color:{c};font-weight:800;'
                            f'margin-top:2px;">{s}{pl:,.0f}원 ({s}{pl_pct:.2f}%)</div>'
                        )
                    else:
                        pl_html = ('<div style="color:#6b6b6b;font-size:12px;">'
                                   '시세 조회 실패</div>')

                    add_count = pos.get("add_count", 0)
                    add_hint = (f" · +{add_count}회 추매" if add_count > 0 else "")
                    st.markdown(
                        f'<div class="stock-card held">'
                        f'<div style="display:flex;align-items:center;gap:16px;">'
                        f'<div style="flex:2;min-width:140px;">'
                        f'<div class="stock-name">{pos["name"]}</div>'
                        f'<div class="stock-ticker">{ticker} · 진입 ₩{entry:,.0f} · {qty}주{add_hint}</div>'
                        f'</div>'
                        f'<div style="flex:0 0 76px;text-align:center;">'
                        f'<div class="small-label">오늘점수</div>'
                        f'{score_html}</div>'
                        f'<div style="flex:1;min-width:140px;text-align:right;">'
                        f'{pl_html}</div>'
                        f'</div></div>',
                        unsafe_allow_html=True,
                    )

                    # ----- Add-to-position + 매도 버튼 -----
                    if CLOUD_MODE:
                        limits = CONFIG.get("portfolio_limits", {})
                        add_amt = int(limits.get("add_amount_krw", 100_000))
                        add_min = float(limits.get("add_min_score", 80))
                        max_adds = int(limits.get("max_adds_per_position", 3))
                        today_score = float(rec_today.get("total_score", 0)) if rec_today is not None else 0
                        can_add = (cur_price is not None
                                   and today_score >= add_min
                                   and add_count < max_adds)
                        price_for_trade = cur_price if cur_price else entry

                        bc1, bc2, bc3 = st.columns([2, 1, 1])
                        with bc1:
                            add_label = f"💰 +{add_amt//10_000}만원 추매"
                            if not can_add:
                                if today_score < add_min:
                                    add_label = f"⚠️ {add_min:.0f}점 미달 ({today_score:.1f})"
                                elif add_count >= max_adds:
                                    add_label = f"🔒 추매 한도 ({max_adds}회)"
                                elif cur_price is None:
                                    add_label = "시세 조회 실패"
                            if st.button(add_label, key=f"add_{ticker}",
                                         use_container_width=True,
                                         disabled=not can_add):
                                if web_add(ticker, price_for_trade, add_amt,
                                           today_score):
                                    st.balloons()
                                    st.rerun()
                        with bc2:
                            if st.button("50% 매도", key=f"recsell50_{ticker}",
                                         use_container_width=True):
                                if web_sell(ticker, price_for_trade, 0.5, "수동 (웹)"):
                                    st.rerun()
                        with bc3:
                            if st.button("전량 매도", key=f"recsell100_{ticker}",
                                         use_container_width=True):
                                if web_sell(ticker, price_for_trade, 1.0, "수동 (웹)"):
                                    st.rerun()

            # ==================================================
            #  2) CHARTS — below, collapsed (static, no touch/hover)
            # ==================================================
            # Config: disable all interactivity so mobile doesn't "select" chart.
            static_cfg = {
                "staticPlot": True,  # no hover, pan, select — behaves like image
                "displayModeBar": False,
                "displaylogo": False,
            }

            with st.expander("📊 상세 차트 (점수 분포 · Top 10 팩터 구성)", expanded=False):
                # Score distribution
                st.markdown(
                    '<div class="small-label" style="margin:6px 0 4px;">점수 분포 (전체 유니버스)</div>',
                    unsafe_allow_html=True,
                )
                fig_dist = go.Figure()
                fig_dist.add_trace(go.Histogram(
                    x=df["total_score"], nbinsx=30,
                    marker=dict(color="#3b82f6", line=dict(width=0)),
                    opacity=0.85,
                ))
                for thr, label, color in [(85, "85", "#0F7B0F"),
                                          (90, "90", "#EAB308"),
                                          (95, "95", "#CC2222")]:
                    fig_dist.add_vline(x=thr, line=dict(color=color, width=1.5, dash="dash"),
                                       annotation_text=label, annotation_position="top",
                                       annotation_font_size=10, annotation_font_color=color)
                fig_dist.update_layout(
                    template="plotly_white", paper_bgcolor="#FFFFFF", plot_bgcolor="#FAFAFA",
                    font=dict(color="#1a1a1a", family="Pretendard, Segoe UI", size=11),
                    margin=dict(l=40, r=20, t=24, b=34), height=240,
                    bargap=0.05, showlegend=False,
                    xaxis=dict(title="총점", range=[0, 100],
                               gridcolor="#EEEEEE", zeroline=False, fixedrange=True),
                    yaxis=dict(title="종목 수", gridcolor="#EEEEEE",
                               zeroline=False, fixedrange=True),
                    dragmode=False,
                )
                st.plotly_chart(fig_dist, use_container_width=True,
                                theme=None, config=static_cfg)

                # Top 10 factor stack
                st.markdown(
                    '<div class="small-label" style="margin:14px 0 4px;">상위 10종목 팩터 구성</div>',
                    unsafe_allow_html=True,
                )
                top10 = df.head(10).copy().iloc[::-1]
                fig_stack = go.Figure()
                weights = CONFIG["scoring"]["factors"]
                for col, name, color, w in [
                    ("mean_reversion_score", "역추세", "#fbbf24", weights["mean_reversion"]),
                    ("quality_score",        "퀄리티", "#a855f7", weights["quality"]),
                    ("supply_demand_score",  "수급",   "#3b82f6", weights["supply_demand"]),
                    ("momentum_score",       "모멘텀", "#22c55e", weights["momentum"]),
                ]:
                    if col not in top10.columns:
                        continue
                    fig_stack.add_trace(go.Bar(
                        y=top10["name"], x=top10[col] * w / 100.0,
                        orientation="h", name=name, marker=dict(color=color),
                    ))
                fig_stack.update_layout(
                    template="plotly_white", paper_bgcolor="#FFFFFF", plot_bgcolor="#FAFAFA",
                    font=dict(color="#1a1a1a", family="Pretendard, Segoe UI", size=11),
                    margin=dict(l=110, r=20, t=10, b=50), height=360,
                    barmode="stack", showlegend=True,
                    legend=dict(orientation="h", yanchor="bottom", y=-0.15,
                                xanchor="center", x=0.5, font=dict(size=10)),
                    xaxis=dict(title="가중 기여 점수", gridcolor="#EEEEEE",
                               zeroline=False, range=[0, 100], fixedrange=True),
                    yaxis=dict(gridcolor="#EEEEEE", zeroline=False, fixedrange=True),
                    dragmode=False,
                )
                st.plotly_chart(fig_stack, use_container_width=True,
                                theme=None, config=static_cfg)

        except Exception:
            st.error("Score load failed")
            st.code(traceback.format_exc())


# === Positions rendering shared ===
def _render_positions(mode_key: str):
    pos_list = [(t, p) for t, p in portfolio["positions"].items()
                if p.get("mode", "simulation") == mode_key]
    if not pos_list:
        if mode_key == "simulation":
            st.markdown(
                '<div class="empty-panel"><div class="empty-emoji">🧪</div>'
                '<div style="color:#1a1a1a;font-weight:700;">모의 보유 없음</div>'
                '<div style="margin-top:6px;">추천 탭에서 매수하세요.</div></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="empty-panel"><div class="empty-emoji">💼</div>'
                '<div style="color:#1a1a1a;font-weight:700;">실전 계좌 미연동</div>'
                '<div style="margin-top:6px;">Phase 5: 증권사 API 연동 · '
                '모의투자 2주 + 백테스트 알파 +3% 통과 후</div></div>',
                unsafe_allow_html=True,
            )
        return

    for ticker, p in pos_list:
        name = p["name"]
        entry = p["entry_price"]
        qty = p["qty"]
        cost = p["cost_krw"]
        entry_date = p["entry_date"]
        score = p.get("entry_score", 0)
        cur = current_prices.get(ticker)
        if cur:
            mv = cur * qty
            pl = (cur - entry) * qty
            pl_pct = (cur / entry - 1) * 100
            pl_color = "#0F7B0F" if pl >= 0 else "#CC2222"
            pl_sign = "+" if pl >= 0 else ""
            cur_html = (
                f'<div style="flex:1;">'
                f'<div class="small-label">현재가</div>'
                f'<div style="font-size:16px;font-weight:700;">₩{cur:,.0f}</div></div>'
                f'<div style="flex:1;">'
                f'<div class="small-label">평가손익</div>'
                f'<div style="font-size:16px;font-weight:800;color:{pl_color};">'
                f'{pl_sign}{pl:,.0f}원</div>'
                f'<div style="font-size:11px;color:{pl_color};font-weight:700;">'
                f'{pl_sign}{pl_pct:.2f}%</div></div>'
            )
        else:
            cur = entry
            mv = cost
            cur_html = (
                f'<div style="flex:1;">'
                f'<div class="small-label">현재가</div>'
                f'<div style="font-size:14px;color:#6b6b6b;">–</div></div>'
                f'<div style="flex:1;">'
                f'<div class="small-label">평가손익</div>'
                f'<div style="font-size:14px;color:#6b6b6b;">시세 조회 실패</div></div>'
            )

        st.markdown(
            f'<div class="stock-card">'
            f'<div style="display:flex;align-items:center;gap:20px;">'
            f'<div style="flex:2;min-width:160px;">'
            f'<div class="stock-name">{name}</div>'
            f'<div class="stock-ticker">{ticker} · 진입 {entry_date} · {qty}주</div>'
            f'<div style="margin-top:8px;"><span class="pill pill-gray">진입점수 {score:.1f}</span></div>'
            f'</div>'
            f'<div style="flex:1;">'
            f'<div class="small-label">진입가</div>'
            f'<div style="font-size:16px;font-weight:700;">₩{entry:,.0f}</div></div>'
            f'{cur_html}'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        if CLOUD_MODE:
            with st.expander(f"💸 {name} 매도", expanded=False):
                sell_price = cur if current_prices.get(ticker) else entry
                st.caption(f"매도 예상가: ₩{sell_price:,.0f}"
                           + ("" if current_prices.get(ticker) else " (시세 조회 실패 — 진입가 사용)"))
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("50% 매도", key=f"sell50_{ticker}",
                                 use_container_width=True):
                        if web_sell(ticker, sell_price, 0.5, "수동 (웹)"):
                            st.rerun()
                with c2:
                    if st.button("100% 매도", key=f"sell100_{ticker}",
                                 use_container_width=True):
                        if web_sell(ticker, sell_price, 1.0, "수동 (웹)"):
                            st.rerun()


with tab_sim:
    _render_positions("simulation")

with tab_real:
    _render_positions("real")


# === History ===
with tab_hist:
    trades = history.get("trades", [])
    if not trades:
        st.markdown(
            '<div class="empty-panel"><div class="empty-emoji">📜</div>'
            '<div style="color:#1a1a1a;font-weight:700;">거래 기록 없음</div></div>',
            unsafe_allow_html=True,
        )
    else:
        buys = [t for t in trades if t.get("action") == "buy"]
        sells = [t for t in trades if t.get("action") == "sell"]
        total_bought = sum(t.get("cost_krw", 0) for t in buys)
        total_sold = sum(t.get("proceeds_krw", 0) for t in sells)
        realized_total = sum(t.get("pnl_krw", 0) for t in sells)

        h1, h2, h3, h4 = st.columns(4)
        with h1:
            st.markdown(kpi_card("매수 건수", f"{len(buys)}건", "blue",
                                 f"누적 {total_bought:,.0f}원"), unsafe_allow_html=True)
        with h2:
            st.markdown(kpi_card("매도 건수", f"{len(sells)}건", "",
                                 f"누적 {total_sold:,.0f}원"), unsafe_allow_html=True)
        with h3:
            c = "green" if realized_total >= 0 else "red"
            s = "+" if realized_total >= 0 else ""
            st.markdown(kpi_card("실현 손익", f"{s}{realized_total:,.0f}원", c, ""),
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
                st.markdown(kpi_card("매도 승률", "–", "", ""), unsafe_allow_html=True)

        st.write("")

        GRID = "110px 90px 1fr 90px 100px 110px 100px 100px 1fr"
        HEAD = (f"display:grid;grid-template-columns:{GRID};padding:11px 16px;"
                "background:#F5F5F5;border-bottom:1px solid #E0E0E0;"
                "font-size:10.5px;font-weight:700;letter-spacing:0.6px;"
                "color:#6b6b6b;text-transform:uppercase;")
        ROW = (f"display:grid;grid-template-columns:{GRID};padding:12px 16px;"
               "border-bottom:1px solid #EEEEEE;font-size:12.5px;align-items:center;"
               "font-variant-numeric:tabular-nums;")

        trades_sorted = sorted(trades,
                               key=lambda t: t.get("exit_date") or t.get("entry_date") or "",
                               reverse=True)

        parts = ['<div class="table-scroll" style="background:#fff;border:1px solid #E5E5E5;border-radius:14px;box-shadow:0 2px 0 rgba(0,0,0,0.03);">']
        parts.append('<div style="min-width:920px;">')
        parts.append(f'<div style="{HEAD}">')
        parts.append('<div>날짜</div><div>구분</div><div>종목</div>')
        parts.append('<div style="text-align:right;">수량</div>')
        parts.append('<div style="text-align:right;">단가</div>')
        parts.append('<div style="text-align:right;">금액</div>')
        parts.append('<div style="text-align:right;">수익률</div>')
        parts.append('<div style="text-align:right;">손익</div>')
        parts.append('<div>사유</div></div>')

        KO = {
            "hard_stop_loss": "하드 손절", "time_stop": "타임 스톱",
            "take_profit_partial": "부분 익절", "trailing_stop": "트레일링 스톱",
            "sell_score_stage2": "매도점수 80+", "sell_score_stage1": "매도점수 60+",
            "manual": "수동", "momentum_reversal": "모멘텀 반전",
            "foreign_sell": "외국인 순매도", "ma5_break": "MA5 이탈",
        }
        for t in trades_sorted:
            action = t.get("action", "")
            date = t.get("exit_date") or t.get("entry_date", "")
            ticker = t.get("ticker", "")
            name = t.get("name", "")
            qty = int(t.get("qty", 0))
            price = float(t.get("price", 0) or 0)
            pnl_pct = t.get("pnl_pct")
            pnl_krw = t.get("pnl_krw")
            reason = t.get("reason", "") or ""

            if action == "buy":
                total = t.get("cost_krw", qty * price)
                pill_cls, pill_text = "pill-blue", "매수"
                pnl_cell = '<span style="color:#6b6b6b;">–</span>'
                profit_cell = pnl_cell
                reason_display = (f'진입점수 {t.get("entry_score",0):.1f}' if "entry_score" in t else "")
            else:
                total = t.get("proceeds_krw", qty * price)
                pill_cls = "pill-green" if (pnl_krw or 0) >= 0 else "pill-red"
                pill_text = "매도"
                if pnl_pct is not None:
                    c = "#0F7B0F" if pnl_pct >= 0 else "#CC2222"
                    s = "+" if pnl_pct >= 0 else ""
                    pnl_cell = f'<span style="color:{c};font-weight:700;">{s}{pnl_pct:.2f}%</span>'
                else:
                    pnl_cell = '–'
                if pnl_krw is not None:
                    c = "#0F7B0F" if pnl_krw >= 0 else "#CC2222"
                    s = "+" if pnl_krw >= 0 else ""
                    profit_cell = f'<span style="color:{c};font-weight:700;">{s}{pnl_krw:,.0f}원</span>'
                else:
                    profit_cell = '–'
                reason_display = reason or "-"

            for eng, kor in KO.items():
                reason_display = reason_display.replace(eng, kor)

            row = (f'<div style="{ROW}">'
                   f'<div style="color:#8a8a8a;font-family:Consolas,monospace;">{date}</div>'
                   f'<div><span class="pill {pill_cls}">{pill_text}</span></div>'
                   f'<div>'
                   f'<div style="color:#1a1a1a;font-weight:700;">{name}</div>'
                   f'<div style="color:#6b6b6b;font-size:10.5px;font-family:Consolas,monospace;">{ticker}</div>'
                   f'</div>'
                   f'<div style="text-align:right;color:#1a1a1a;">{qty:,}주</div>'
                   f'<div style="text-align:right;color:#1a1a1a;">₩{price:,.0f}</div>'
                   f'<div style="text-align:right;color:#1a1a1a;font-weight:700;">₩{total:,.0f}</div>'
                   f'<div style="text-align:right;">{pnl_cell}</div>'
                   f'<div style="text-align:right;">{profit_cell}</div>'
                   f'<div style="color:#8a8a8a;font-size:11.5px;">{reason_display}</div>'
                   f'</div>')
            parts.append(row)

        parts.append('</div></div>')
        st.markdown("".join(parts), unsafe_allow_html=True)


# === Performance ===
with tab_perf:
    sells_all = [t for t in history.get("trades", []) if t.get("action") == "sell"]
    if not sells_all:
        st.markdown(
            '<div class="empty-panel"><div class="empty-emoji">📊</div>'
            '<div style="color:#1a1a1a;font-weight:700;">매도 거래 없음</div>'
            '<div style="margin-top:6px;">첫 매도부터 누적 손익 차트가 표시됩니다.</div></div>',
            unsafe_allow_html=True,
        )
    else:
        df_s = pd.DataFrame(sells_all)
        df_s["exit_date"] = pd.to_datetime(df_s["exit_date"])
        df_s = df_s.sort_values("exit_date")
        df_s["cum_pnl"] = df_s["pnl_krw"].cumsum()
        total = df_s["pnl_krw"].sum()
        wins = (df_s["pnl_krw"] > 0).sum()
        rate = wins / len(df_s) * 100
        avg = df_s["pnl_pct"].mean()

        p1, p2, p3, p4 = st.columns(4)
        with p1: st.markdown(kpi_card("TRADES", f"{len(df_s)}", ""), unsafe_allow_html=True)
        with p2: st.markdown(kpi_card("WIN RATE", f"{rate:.1f}%",
                                      "green" if rate>=50 else "red"), unsafe_allow_html=True)
        with p3: st.markdown(kpi_card("AVG RETURN", f"{avg:+.2f}%",
                                      "green" if avg>=0 else "red"), unsafe_allow_html=True)
        with p4: st.markdown(kpi_card("TOTAL P/L", f"{total:+,.0f}원",
                                      "green" if total>=0 else "red"), unsafe_allow_html=True)

        st.write("")
        line_color = "#0F7B0F" if total >= 0 else "#CC2222"
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_s["exit_date"], y=df_s["cum_pnl"],
            mode="lines+markers",
            line=dict(color=line_color, width=2.5),
            marker=dict(size=7, color=line_color, line=dict(width=1, color="#fff")),
            fill="tozeroy",
            fillcolor="rgba(15,123,15,0.1)" if total>=0 else "rgba(204,34,34,0.1)",
        ))
        fig.update_layout(
            template="plotly_white",
            paper_bgcolor="#FAF9F6", plot_bgcolor="#FFFFFF",
            font=dict(color="#1a1a1a", family="Pretendard, Segoe UI", size=12),
            margin=dict(l=40, r=20, t=20, b=40), height=360,
            xaxis=dict(gridcolor="#EEEEEE", showgrid=True, zeroline=False, fixedrange=True),
            yaxis=dict(gridcolor="#EEEEEE", showgrid=True, zeroline=True,
                       zerolinecolor="#CCCCCC", title="누적 손익 (원)", fixedrange=True),
            showlegend=False, dragmode=False,
        )
        st.plotly_chart(
            fig, use_container_width=True, theme=None,
            config={"staticPlot": True, "displayModeBar": False, "displaylogo": False},
        )


# === Info ===
with tab_info:
    col1, col2 = st.columns(2)
    w = CONFIG["scoring"]["factors"]
    with col1:
        st.markdown(
            f'<div class="stock-card">'
            f'<div class="small-label">SCORING WEIGHTS</div>'
            f'<div style="margin-top:10px;display:grid;grid-template-columns:repeat(2,1fr);gap:10px;">'
            f'<div>모멘텀 <b style="color:#0F7B0F;">{w["momentum"]}%</b></div>'
            f'<div>수급 <b style="color:#3182F6;">{w["supply_demand"]}%</b></div>'
            f'<div>퀄리티 <b style="color:#a855f7;">{w["quality"]}%</b></div>'
            f'<div>역추세 <b style="color:#EAB308;">{w["mean_reversion"]}%</b></div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
    with col2:
        sr = CONFIG["sell_rules"]
        st.markdown(
            f'<div class="stock-card">'
            f'<div class="small-label">SELL RULES</div>'
            f'<div style="margin-top:10px;font-size:12.5px;line-height:1.8;">'
            f'· 하드 손절 <b style="color:#CC2222;">{sr["hard_stop_loss_pct"]}%</b><br>'
            f'· 부분 익절 <b style="color:#0F7B0F;">+{sr["hard_take_profit_partial_pct"]}%</b> 에서 50%<br>'
            f'· 타임 스톱 <b>{sr["time_stop_days"]}</b> 거래일<br>'
            f'· 트레일링 스톱 <b style="color:#EAB308;">{sr["trailing_stop_pct"]}%</b><br>'
            f'· 매도점수 {sr["sell_score_stage1"]}+ → 50% · {sr["sell_score_stage2"]}+ → 100%'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    # Sync status
    if CLOUD_MODE:
        st.markdown(
            '<div style="margin-top:14px;padding:14px;background:#EAFBEF;'
            'border:1px solid #B8E5C0;border-radius:14px;box-shadow:0 2px 0 rgba(0,0,0,0.03);">'
            '<span class="pill pill-green">● 클라우드 연동</span> '
            'GitHub API를 통해 매수/매도 및 데이터 읽기 가능. '
            '매일 저녁 18:30 (KST) GitHub Actions가 자동으로 점수 계산.'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="margin-top:14px;padding:14px;background:#FFF8E4;'
            'border:1px solid #F0D590;border-radius:14px;color:#7A4F00;box-shadow:0 2px 0 rgba(0,0,0,0.03);">'
            '<span class="pill pill-gold">● 로컬 모드</span> '
            'Streamlit Cloud → Settings → Secrets 에 <code>GITHUB_TOKEN</code> 추가하면 '
            '웹에서 매수/매도가 활성화됩니다.'
            '</div>',
            unsafe_allow_html=True,
        )

    import sys
    st.markdown(
        f'<div style="margin-top:14px;color:#6b6b6b;font-size:11px;text-align:center;">'
        f'Python {sys.version.split()[0]} · Streamlit {st.__version__} · '
        f'<a href="https://github.com/junkyulee2/stock-advisor" style="color:#3182F6;">GitHub</a>'
        f'</div>',
        unsafe_allow_html=True,
    )
