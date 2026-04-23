"""Main window — header with metrics, tab bar, action footer."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from src import portfolio as pf
from src.utils import (
    PROJECT_ROOT, iso_today, load_config, load_json, save_json,
    previous_trading_day,
)

from .styles import COLORS, main_qss
from .tabs import (
    BacktestTab, HistoryTab, PerformanceTab, PositionsTab,
    RecommendationsTab, SettingsTab,
)
from .widgets import MetricCard, SectionHeader


class AppState:
    """Shared state across tabs."""

    def __init__(self) -> None:
        self.config = load_config()
        self._reload()

    def _reload(self) -> None:
        self.portfolio = pf.load_portfolio(PROJECT_ROOT / self.config["paths"]["portfolio"])
        self.history = pf.load_history(PROJECT_ROOT / self.config["paths"]["history"])

    def save(self) -> None:
        save_json(PROJECT_ROOT / self.config["paths"]["portfolio"], self.portfolio)
        save_json(PROJECT_ROOT / self.config["paths"]["history"], self.history)

    def latest_score_file(self) -> Path | None:
        d = PROJECT_ROOT / self.config["paths"]["scores_dir"]
        if not d.exists():
            return None
        files = sorted(d.glob("scores_*.json"))
        return files[-1] if files else None

    @property
    def latest_score_file(self):
        f = self._latest()
        return f.name if f else "없음"

    def _latest(self):
        d = PROJECT_ROOT / self.config["paths"]["scores_dir"]
        if not d.exists():
            return None
        files = sorted(d.glob("scores_*.json"))
        return files[-1] if files else None

    def latest_scores(self) -> pd.DataFrame | None:
        f = self._latest()
        if not f:
            return None
        df = pd.read_json(f)
        if "ticker" not in df.columns and "index" in df.columns:
            df = df.rename(columns={"index": "ticker"})
        if "total_score" in df.columns:
            df = df.sort_values("total_score", ascending=False)
        return df


# ============================================================
#  Background worker for running run_daily pipeline
# ============================================================

class PipelineWorker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, mode: str = "scores", limit: int | None = None, as_of: str | None = None) -> None:
        super().__init__()
        self.mode = mode
        self.limit = limit
        self.as_of = as_of

    def run(self) -> None:
        try:
            import importlib
            self.log.emit(f"[{_ts()}] pipeline start | mode={self.mode} | limit={self.limit}")
            from src.utils import load_config, previous_trading_day
            import run_daily as rd
            importlib.reload(rd)

            config = load_config()
            as_of = self.as_of or previous_trading_day()
            if self.mode in ("scores", "both"):
                self.log.emit(f"[{_ts()}] fetching data…")
                df = rd.compute_daily_scores(config, as_of, limit=self.limit)
                if df is not None and not df.empty:
                    rd.save_daily_scores(df, config, as_of)
                    picks = rd.recommend_top3(df, config)
                    self.log.emit(f"[{_ts()}] Top picks: {[(p['ticker'], round(p['total_score'],1)) for p in picks]}")
                else:
                    self.log.emit(f"[{_ts()}] no scores produced")
            if self.mode in ("signals", "both"):
                self.log.emit(f"[{_ts()}] checking sell signals…")
                alerts = rd.check_sell_signals(config, as_of)
                self.log.emit(f"[{_ts()}] sell alerts: {len(alerts)}")
            self.done.emit(True, "완료")
        except Exception as e:
            self.log.emit(f"[{_ts()}] ERROR: {e}")
            self.done.emit(False, str(e))


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ============================================================
#  Main window
# ============================================================

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.state = AppState()
        icon_path = PROJECT_ROOT / "assets" / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowTitle("춘큐 스탁 어드바이져")
        self.resize(1400, 900)
        self.setStyleSheet(main_qss())
        self._worker: PipelineWorker | None = None
        self._build()
        self._refresh_metrics()

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Header ----
        header = QWidget()
        header.setObjectName("Header")
        header.setFixedHeight(84)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 10, 20, 10)
        hl.setSpacing(16)

        # icon badge
        badge = QFrame()
        badge.setObjectName("IconBadge")
        badge.setFixedSize(60, 60)
        bl = QVBoxLayout(badge)
        bl.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ipath = PROJECT_ROOT / "assets" / "icon.ico"
        if ipath.exists():
            pix = QPixmap(str(ipath)).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            icon_label.setPixmap(pix)
        bl.addWidget(icon_label)
        hl.addWidget(badge)

        # title block
        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        t = QLabel("춘큐 스탁 어드바이져")
        t.setObjectName("HeaderTitle")
        s = QLabel("Momentum × Supply-Demand + Quality Guard")
        s.setObjectName("HeaderSubtitle")
        title_block.addWidget(t)
        title_block.addWidget(s)
        tw = QWidget()
        tw.setLayout(title_block)
        hl.addWidget(tw)

        hl.addStretch()

        # metrics inline (compact)
        self._m_positions = MetricCard("POSITIONS", "0")
        self._m_realized = MetricCard("REALIZED P/L", "0원")
        self._m_winrate = MetricCard("WIN RATE", "-")
        self._m_trades = MetricCard("TRADES", "0")
        for w in (self._m_positions, self._m_trades, self._m_winrate, self._m_realized):
            w.setFixedWidth(180)
            hl.addWidget(w)

        root.addWidget(header)

        # ---- Tabs ----
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)

        self._tab_rec = RecommendationsTab(self.state)
        self._tab_sim = PositionsTab(self.state, mode="simulation")
        self._tab_real = PositionsTab(self.state, mode="real")
        self._tab_hist = HistoryTab(self.state)
        self._tab_perf = PerformanceTab(self.state)
        self._tab_bt = BacktestTab(self.state)
        self._tab_set = SettingsTab(self.state)

        self._tab_rec.bought.connect(self._refresh_all)
        self._tab_sim.sold.connect(self._refresh_all)
        self._tab_real.sold.connect(self._refresh_all)

        self._tabs.addTab(self._tab_rec, "🎯  오늘의 추천")
        self._tabs.addTab(self._tab_sim, "🧪  모의투자")
        self._tabs.addTab(self._tab_real, "💼  보유중")
        self._tabs.addTab(self._tab_hist, "📜  거래 이력")
        self._tabs.addTab(self._tab_perf, "📊  성과")
        self._tabs.addTab(self._tab_bt, "🔬  백테스트")
        self._tabs.addTab(self._tab_set, "⚙️  설정")
        root.addWidget(self._tabs, 1)

        # ---- Footer / log ----
        footer = QFrame()
        footer.setFixedHeight(160)
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(18, 6, 18, 14)
        fl.setSpacing(6)

        # Action row
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._run_full_btn = QPushButton("오늘 점수 계산 (전체)")
        self._run_full_btn.setObjectName("PrimaryBtn")
        self._run_full_btn.setFixedHeight(34)
        self._run_full_btn.clicked.connect(lambda: self._run_pipeline(mode="scores", limit=None))
        self._run_quick_btn = QPushButton("빠른 계산 (Top 30)")
        self._run_quick_btn.setObjectName("GhostBtn")
        self._run_quick_btn.setFixedHeight(34)
        self._run_quick_btn.clicked.connect(lambda: self._run_pipeline(mode="scores", limit=30))
        self._sig_btn = QPushButton("매도 시그널 체크")
        self._sig_btn.setObjectName("GhostBtn")
        self._sig_btn.setFixedHeight(34)
        self._sig_btn.clicked.connect(lambda: self._run_pipeline(mode="signals", limit=None))

        action_row.addWidget(self._run_full_btn)
        action_row.addWidget(self._run_quick_btn)
        action_row.addWidget(self._sig_btn)
        action_row.addStretch()

        self._status_label = QLabel("준비됨")
        self._status_label.setStyleSheet(f"color:{COLORS['text_muted']}; font-size:11px;")
        action_row.addWidget(self._status_label)

        fl.addLayout(action_row)

        # Log
        self._log = QPlainTextEdit()
        self._log.setObjectName("LogView")
        self._log.setReadOnly(True)
        self._log.setFixedHeight(100)
        self._log.appendPlainText(f"[{_ts()}] 춘큐 스탁 어드바이져 started.")
        fl.addWidget(self._log)

        root.addWidget(footer)

    # -------- state refresh --------
    def _refresh_all(self) -> None:
        self._tab_rec.refresh()
        self._tab_sim.refresh()
        self._tab_real.refresh()
        self._tab_hist.refresh()
        self._tab_perf.refresh()
        self._refresh_metrics()

    def _refresh_metrics(self) -> None:
        trades = self.state.history.get("trades", [])
        sells = [t for t in trades if t["action"] == "sell"]
        realized = sum(t.get("pnl_krw", 0) for t in sells)
        wins = sum(1 for t in sells if t.get("pnl_krw", 0) > 0)
        total_trades = len(trades)

        self._m_positions.set_value(str(len(self.state.portfolio["positions"])))
        self._m_trades.set_value(str(total_trades))
        if sells:
            wr = wins / len(sells) * 100
            self._m_winrate.set_value(f"{wr:.1f}%", "green" if wr >= 50 else "red")
            self._m_realized.set_value(
                f"{realized:+,.0f}원",
                "green" if realized >= 0 else "red",
            )
        else:
            self._m_winrate.set_value("-")
            self._m_realized.set_value("0원")

    # -------- pipeline --------
    def _run_pipeline(self, mode: str, limit: int | None) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "진행 중", "이미 파이프라인이 실행 중입니다.")
            return
        self._status_label.setText("실행 중…")
        self._worker = PipelineWorker(mode=mode, limit=limit)
        self._worker.log.connect(self._append_log)
        self._worker.done.connect(self._on_pipeline_done)
        self._worker.start()

    def _append_log(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    def _on_pipeline_done(self, ok: bool, msg: str) -> None:
        self._status_label.setText("완료" if ok else f"실패: {msg}")
        self._append_log(f"[{_ts()}] pipeline {'done' if ok else 'failed'}: {msg}")
        if ok:
            self._refresh_all()
