"""FastAPI app for 춘규주식. Server-side rendered with Jinja2 + HTMX."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Make src/ importable when running as a script
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from webapp import services, views  # noqa: E402
from webapp.data_layer import latest_scores  # noqa: E402
from webapp.price_fetcher import fetch_current_prices  # noqa: E402

app = FastAPI(title="춘규주식 — Stock Advisor", version="2.0.0")

WEBAPP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEBAPP_DIR / "templates"))
templates.env.cache = None  # avoid Python 3.14 + jinja2 weakref tuple cache bug
app.mount("/static", StaticFiles(directory=str(WEBAPP_DIR / "static")), name="static")


# ---------- Jinja filters ----------

def _krw(value: float | int | None) -> str:
    if value is None:
        return "₩0"
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "₩0"
    sign = "-" if n < 0 else ""
    return f"{sign}₩{abs(n):,}"


def _krw_signed(value: float | int | None) -> str:
    if value is None:
        return "₩0"
    n = int(value)
    if n > 0:
        return f"+₩{n:,}"
    if n < 0:
        return f"−₩{abs(n):,}"
    return "₩0"


def _pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:+.{digits}f}%"


def _number(value: float | int | None) -> str:
    if value is None:
        return "0"
    return f"{int(value):,}"


def _score(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f}"


templates.env.filters["krw"] = _krw
templates.env.filters["krw_signed"] = _krw_signed
templates.env.filters["pct"] = _pct
templates.env.filters["num"] = _number
templates.env.filters["score"] = _score


# ---------- page routes ----------

@app.get("/", response_class=HTMLResponse)
def page_dashboard(request: Request):
    ctx = views.build_dashboard_context()
    ctx["active"] = "dashboard"
    return templates.TemplateResponse(request=request, name="dashboard.html", context=ctx)


@app.get("/recommendations", response_class=HTMLResponse)
def page_recommendations(request: Request):
    ctx = views.build_recommendations_context()
    ctx["request"] = request
    ctx["active"] = "recommendations"
    return templates.TemplateResponse(request, "recommendations.html", ctx)


@app.get("/holdings", response_class=HTMLResponse)
def page_holdings(request: Request):
    ctx = views.build_holdings_context()
    ctx["request"] = request
    ctx["active"] = "holdings"
    return templates.TemplateResponse(request, "holdings.html", ctx)


@app.get("/history", response_class=HTMLResponse)
def page_history(request: Request):
    ctx = views.build_history_context()
    ctx["request"] = request
    ctx["active"] = "history"
    return templates.TemplateResponse(request, "history.html", ctx)


@app.get("/analytics", response_class=HTMLResponse)
def page_analytics(request: Request):
    ctx = views.build_analytics_context()
    ctx["request"] = request
    ctx["active"] = "analytics"
    return templates.TemplateResponse(request, "analytics.html", ctx)


# ---------- action endpoints (HTMX form posts) ----------

@app.post("/actions/buy", response_class=HTMLResponse)
def act_buy(request: Request,
            ticker: str = Form(...),
            amount_krw: int = Form(...)):
    scores, _ = latest_scores()
    rec = next((s for s in scores if s["ticker"] == ticker), None)
    if rec is None:
        return _toast_response(False, "종목을 찾을 수 없습니다 (점수 만료?)")
    ok, msg = services.buy(rec, amount_krw)
    return _toast_response(ok, msg, refresh=ok)


@app.post("/actions/add", response_class=HTMLResponse)
def act_add(request: Request,
            ticker: str = Form(...),
            amount_krw: int = Form(...)):
    scores, _ = latest_scores()
    rec = next((s for s in scores if s["ticker"] == ticker), None)
    if rec is None:
        # Allow add at current price even without score
        prices = fetch_current_prices((ticker,))
        price = float(prices.get(ticker, 0))
        score = 0.0
    else:
        price = float(rec.get("close", 0) or 0)
        score = float(rec.get("total_score", 0) or 0)
    if price <= 0:
        return _toast_response(False, "가격 조회 실패")
    ok, msg = services.add(ticker, price, amount_krw, score)
    return _toast_response(ok, msg, refresh=ok)


@app.post("/actions/sell", response_class=HTMLResponse)
def act_sell(request: Request,
             ticker: str = Form(...),
             ratio: float = Form(1.0),
             reason: str = Form("manual")):
    prices = fetch_current_prices((ticker,))
    price = float(prices.get(ticker, 0))
    if price <= 0:
        return _toast_response(False, "현재가 조회 실패")
    ratio = max(0.01, min(1.0, ratio))
    ok, msg = services.sell(ticker, price, ratio, reason)
    return _toast_response(ok, msg, refresh=ok)


@app.post("/actions/refresh", response_class=HTMLResponse)
def act_refresh(request: Request):
    ok, msg = services.trigger_refresh()
    if not ok:
        # Failure (e.g. local mode, missing token) — show toast in refresh region
        return HTMLResponse(_refresh_toast(False, msg))
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return HTMLResponse(_refresh_banner_pending(since, status="queued", elapsed=0,
                                                first=True))


@app.get("/api/refresh-status", response_class=HTMLResponse)
def api_refresh_status(since: str = ""):
    """Polled by the refresh banner every few seconds. Returns updated banner HTML.

    `since` is the ISO timestamp captured when the user clicked the button.
    Only workflow runs created at-or-after `since` are considered "ours".
    """
    info = services.workflow_status()
    elapsed = _elapsed_seconds(since)

    if info is None:
        # Cloud not configured or API hiccup — keep waiting up to ~30s
        if elapsed < 30:
            return HTMLResponse(_refresh_banner_pending(since, "queued", elapsed))
        return HTMLResponse(_refresh_banner_fail(
            run_url="https://github.com/junkyulee2/stock-advisor/actions",
            reason="워크플로우 상태 조회 실패",
        ))

    created = info.get("created_at", "") or ""
    # Wait up to ~20s for our new dispatch to register in the API
    if since and created < since:
        if elapsed < 20:
            return HTMLResponse(_refresh_banner_pending(since, "queued", elapsed,
                                                       sub="GitHub에 워크플로우 등록 중..."))
        # Took too long — still reflect the most recent run (safer than spinning forever)

    status = info.get("status")
    conclusion = info.get("conclusion")
    run_url = info.get("html_url", "https://github.com/junkyulee2/stock-advisor/actions")

    if status == "completed":
        if conclusion == "success":
            return HTMLResponse(_refresh_banner_done(elapsed, run_url))
        return HTMLResponse(_refresh_banner_fail(run_url, reason=f"워크플로우 {conclusion}"))

    return HTMLResponse(_refresh_banner_pending(since, status or "queued", elapsed))


@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz():
    """Lightweight liveness ping (no GitHub/FDR calls). For UptimeRobot keepalive.

    Accepts both GET and HEAD — UptimeRobot defaults to HEAD requests.
    """
    return JSONResponse({"ok": True, "service": "chungyu-stock", "version": "2.0"})


@app.get("/api/workflow-status")
def api_workflow_status():
    info = services.workflow_status()
    if info is None:
        return JSONResponse({"available": False})
    return JSONResponse({"available": True, **info})


@app.get("/api/debug")
def api_debug():
    """Diagnose cloud connection state. Strips token values."""
    import os
    from src import cloud_store
    from webapp import data_layer

    result: dict = {
        "env_GITHUB_TOKEN_set": bool(os.environ.get("GITHUB_TOKEN")),
        "env_GITHUB_REPO": os.environ.get("GITHUB_REPO") or "(not set; defaulting)",
        "cloud_store.is_configured": cloud_store.is_configured(),
        "data_layer.CLOUD_MODE": data_layer.CLOUD_MODE,
    }
    try:
        data, sha = cloud_store.read_json("data/portfolio.json")
        result["read_portfolio"] = "ok"
        result["positions_count"] = len(data.get("positions", {})) if data else 0
        result["sha_first8"] = (sha or "")[:8]
    except Exception as e:
        result["read_portfolio"] = "FAIL"
        result["error_type"] = type(e).__name__
        result["error_msg"] = str(e)[:300]
    return JSONResponse(result)


# ---------- helpers ----------

def _toast_response(ok: bool, msg: str, refresh: bool = False) -> HTMLResponse:
    """Returns a small toast banner. HX-Refresh header reloads the full page."""
    color_class = "toast-ok" if ok else "toast-err"
    icon = "✅" if ok else "⚠"
    html = (f'<div class="toast {color_class}">'
            f'<span class="toast-icon">{icon}</span>'
            f'<span class="toast-msg">{msg}</span>'
            f'</div>')
    headers = {}
    if refresh:
        headers["HX-Refresh"] = "true"
    return HTMLResponse(html, headers=headers)


# ---------- refresh-banner helpers ----------

_STATUS_LABEL = {
    "queued": "GitHub Actions 큐 대기 중",
    "in_progress": "데이터 수집 + 점수 계산 중",
    "waiting": "워크플로우 대기 중",
}


def _elapsed_seconds(since_iso: str) -> int:
    if not since_iso:
        return 0
    try:
        s = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - s).total_seconds()))
    except Exception:
        return 0


def _fmt_mmss(secs: int) -> str:
    return f"{secs // 60}:{secs % 60:02d}"


def _refresh_toast(ok: bool, msg: str) -> str:
    icon = "✅" if ok else "⚠"
    cls = "ok" if ok else "fail"
    return (f'<div id="refresh-banner" class="refresh-banner {cls}">'
            f'<div class="rb-icon">{icon}</div>'
            f'<div class="rb-text"><strong>{msg}</strong></div>'
            f'<button type="button" class="rb-close" '
            f'onclick="document.getElementById(\'refresh-banner\').remove()">×</button>'
            f'</div>')


def _refresh_banner_pending(since: str, status: str, elapsed: int,
                            sub: str | None = None, first: bool = False) -> str:
    label = sub or _STATUS_LABEL.get(status, "처리 중")
    # Initial swap loads immediately then polls; subsequent swaps just poll.
    trigger = ('load delay:2s, every 5s' if first else 'every 5s')
    return (
        f'<div id="refresh-banner" class="refresh-banner pending"'
        f' hx-get="/api/refresh-status?since={since}"'
        f' hx-trigger="{trigger}"'
        f' hx-swap="outerHTML">'
        f'  <div class="rb-spinner"></div>'
        f'  <div class="rb-text">'
        f'    <strong>점수 재계산 진행 중</strong>'
        f'    <span>{label}</span>'
        f'  </div>'
        f'  <div class="rb-elapsed mono">{_fmt_mmss(elapsed)}</div>'
        f'</div>'
    )


def _refresh_banner_done(elapsed: int, run_url: str) -> str:
    mins = elapsed // 60
    secs = elapsed % 60
    duration = f"{mins}분 {secs}초" if mins else f"{secs}초"
    return (
        f'<div id="refresh-banner" class="refresh-banner done">'
        f'  <div class="rb-icon">✅</div>'
        f'  <div class="rb-text">'
        f'    <strong>점수 재계산 완료</strong>'
        f'    <span>{duration} 걸렸습니다. 새 점수를 확인하세요.</span>'
        f'  </div>'
        f'  <button type="button" class="btn-primary rb-action"'
        f' onclick="location.reload()">새 점수 보기 →</button>'
        f'  <a class="rb-log" href="{run_url}" target="_blank" rel="noopener">로그</a>'
        f'</div>'
    )


def _refresh_banner_fail(run_url: str, reason: str = "오류") -> str:
    return (
        f'<div id="refresh-banner" class="refresh-banner fail">'
        f'  <div class="rb-icon">❌</div>'
        f'  <div class="rb-text">'
        f'    <strong>점수 재계산 실패</strong>'
        f'    <span>{reason}</span>'
        f'  </div>'
        f'  <a class="btn-primary rb-action" href="{run_url}" target="_blank"'
        f' rel="noopener">GitHub 로그 보기</a>'
        f'  <button type="button" class="rb-close"'
        f' onclick="document.getElementById(\'refresh-banner\').remove()">×</button>'
        f'</div>'
    )
