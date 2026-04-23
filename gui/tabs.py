"""Tab widgets — Recommendations, Positions, History, Performance, Backtest, Settings."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from src import data_collector as dc
from src import portfolio as pf
from src import sell_signals as ss
from src.utils import load_config, save_json, PROJECT_ROOT

from .dialogs import BuyAmountDialog
from .styles import COLORS
from .widgets import MetricCard, Panel, Pill, SectionHeader, StockCard


# ============================================================
#  Recommendations tab
# ============================================================

class RecommendationsTab(QWidget):
    bought = pyqtSignal()

    def __init__(self, state) -> None:
        super().__init__()
        self.state = state
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        hdr = SectionHeader("오늘의 추천", "전일 종가 기준 · Top Picks")
        root.addWidget(hdr)

        # top info strip
        info = QHBoxLayout()
        info.setSpacing(10)
        self._regime_pill = Pill("시장 국면: -", "gray")
        self._file_label = QLabel("점수 파일 없음")
        self._file_label.setStyleSheet(f"color:{COLORS['text_muted']}; font-size:11px;")
        self._refresh_btn = QPushButton("다시 불러오기")
        self._refresh_btn.setObjectName("GhostBtn")
        self._refresh_btn.clicked.connect(self.refresh)
        info.addWidget(self._regime_pill)
        info.addWidget(self._file_label)
        info.addStretch()
        info.addWidget(self._refresh_btn)
        root.addLayout(info)

        # cards area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._container = QWidget()
        self._cards_layout = QVBoxLayout(self._container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(10)
        self._scroll.setWidget(self._container)
        root.addWidget(self._scroll, 1)

        self.refresh()

    def refresh(self) -> None:
        # clear existing cards
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        scores = self.state.latest_scores()
        if scores is None or scores.empty:
            empty = Panel("점수 파일 없음")
            empty.body().addWidget(QLabel("run_daily.py --mode scores 를 실행해주세요.\n우측 하단 '오늘 계산' 버튼을 눌러도 됩니다."))
            self._cards_layout.addWidget(empty)
            self._cards_layout.addStretch()
            return

        regime = scores["regime"].iloc[0] if "regime" in scores.columns else "-"
        self._regime_pill.setText(f"시장 국면: {regime}")
        self._file_label.setText(f"기준: {self.state.latest_score_file}")

        min_score = self.state.config["portfolio_limits"]["min_score_to_buy"]
        shown = scores.head(10)
        for _, row in shown.iterrows():
            rec = row.to_dict()
            rec["total_score"] = float(rec.get("total_score", 0))
            held = rec.get("ticker") in self.state.portfolio["positions"]
            card = StockCard(rec, held=held)
            card.buy_clicked.connect(self._on_buy)
            self._cards_layout.addWidget(card)

        self._cards_layout.addStretch()

    def _on_buy(self, rec: dict) -> None:
        price = float(rec.get("close", 0))
        default_amount = int(rec.get("amount_krw", 0))
        if price <= 0:
            QMessageBox.warning(self, "매수 불가", "가격이 0입니다.")
            return

        # Open amount-picker dialog
        dlg = BuyAmountDialog(rec, default_amount=default_amount, parent=self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        amount = dlg.chosen_amount
        if not amount or amount <= 0:
            return

        try:
            pos = pf.buy(
                self.state.portfolio,
                ticker=rec["ticker"],
                name=rec.get("name", ""),
                price=price,
                amount_krw=amount,
                score=float(rec["total_score"]),
            )
            # Mark as simulation (paper trading) by default.
            pos["mode"] = "simulation"
            pf.record_buy_history(self.state.history, pos)
            self.state.save()
            QMessageBox.information(
                self, "모의 매수 완료",
                f"{rec.get('name')} {pos['qty']}주 @ {price:,.0f}원\n총 {pos['cost_krw']:,.0f}원",
            )
            self.bought.emit()
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "오류", str(e))


# ============================================================
#  Positions tab
# ============================================================

class PositionsTab(QWidget):
    sold = pyqtSignal()

    # Filter by mode. "simulation" = paper trading, "real" = live.
    def __init__(self, state, mode: str = "simulation") -> None:
        super().__init__()
        self.state = state
        self.mode = mode
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        title = "모의투자" if self.mode == "simulation" else "보유중"
        sub = "가상 자금으로 연습 · 실시간 매도 시그널 포함" if self.mode == "simulation" \
              else "실제 증권사 계좌 연동 (Phase 5 예정)"
        hdr = SectionHeader(title, sub)
        root.addWidget(hdr)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._container = QWidget()
        self._cards_layout = QVBoxLayout(self._container)
        self._cards_layout.setSpacing(10)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(self._container)
        root.addWidget(self._scroll, 1)
        self.refresh()

    def _filtered_positions(self) -> dict:
        """Return positions matching this tab's mode. Default legacy = simulation."""
        out = {}
        for t, p in self.state.portfolio["positions"].items():
            m = p.get("mode", "simulation")
            if m == self.mode:
                out[t] = p
        return out

    def refresh(self) -> None:
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        positions = self._filtered_positions()
        if not positions:
            if self.mode == "simulation":
                empty = Panel("모의 보유 없음")
                empty.body().addWidget(QLabel(
                    "아직 모의 매수한 종목이 없습니다.\n"
                    "'오늘의 추천' 탭에서 가상 매수 버튼으로 시작하세요."
                ))
            else:
                empty = Panel("실전 계좌 미연동")
                empty.body().addWidget(QLabel(
                    "실제 증권사 계좌 연동은 Phase 5에서 구현 예정입니다.\n\n"
                    "조건:\n"
                    "  · 5년 백테스트 검증 통과\n"
                    "  · 모의투자 2주 이상 실제 운영\n"
                    "  · KOSPI 대비 알파 +3% 이상\n\n"
                    "그 전까지는 모의투자 탭에서 충분히 연습하세요."
                ))
            self._cards_layout.addWidget(empty)
            self._cards_layout.addStretch()
            return

        for ticker, pos in list(positions.items()):
            card = self._make_position_card(ticker, pos)
            self._cards_layout.addWidget(card)
        self._cards_layout.addStretch()

    def _make_position_card(self, ticker: str, pos: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("Panel")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        current_price = self._fetch_price(ticker) or pos["entry_price"]
        ret_pct = (current_price / pos["entry_price"] - 1) * 100
        ret_color = "green" if ret_pct >= 0 else "red"

        # top row
        top = QHBoxLayout()
        left = QVBoxLayout()
        name = QLabel(f"{pos['name']}  ")
        name.setObjectName("StockName")
        meta = QLabel(f"{ticker} · 진입 {pos['entry_date']}")
        meta.setObjectName("StockTicker")
        left.addWidget(name)
        left.addWidget(meta)
        lw = QWidget()
        lw.setLayout(left)
        top.addWidget(lw, 2)

        metric1 = MetricCard("진입가", f"{pos['entry_price']:,.0f}")
        metric2 = MetricCard("현재가", f"{current_price:,.0f}")
        metric3 = MetricCard(
            "수익률", f"{ret_pct:+.2f}%",
            color=ret_color,
        )
        top.addWidget(metric1)
        top.addWidget(metric2)
        top.addWidget(metric3)
        lay.addLayout(top)

        # sell signal check
        try:
            from datetime import timedelta
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d")
            pdf = dc.get_ohlcv(ticker, start, end)
            fdf = dc.get_net_purchases(ticker)
            decision = ss.decide_exit(pos, pdf, fdf, current_price, self.state.config)
        except Exception:
            decision = None

        # bottom row — actions
        bot = QHBoxLayout()
        if decision:
            sig_lbl = QLabel(f"🔔 매도 시그널: {decision['reason']}")
            sig_lbl.setStyleSheet(f"color:{COLORS['warn']}; font-weight:700;")
            bot.addWidget(sig_lbl, 1)
            btn = QPushButton(f"{int(decision['sell_ratio']*100)}% 매도")
            btn.setObjectName("DangerBtn")
            btn.clicked.connect(
                lambda: self._do_sell(ticker, current_price, decision["sell_ratio"], decision["reason"])
            )
            bot.addWidget(btn)
        else:
            sig_lbl = QLabel("시그널 없음 (보유 유지)")
            sig_lbl.setStyleSheet(f"color:{COLORS['text_muted']};")
            bot.addWidget(sig_lbl, 1)

        half_btn = QPushButton("수동 50% 매도")
        half_btn.setObjectName("GhostBtn")
        half_btn.clicked.connect(lambda: self._do_sell(ticker, current_price, 0.5, "manual"))
        bot.addWidget(half_btn)

        full_btn = QPushButton("수동 전량 매도")
        full_btn.setObjectName("GhostBtn")
        full_btn.clicked.connect(lambda: self._do_sell(ticker, current_price, 1.0, "manual"))
        bot.addWidget(full_btn)

        lay.addLayout(bot)
        return card

    def _fetch_price(self, ticker: str) -> Optional[float]:
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

    def _do_sell(self, ticker: str, price: float, ratio: float, reason: str) -> None:
        try:
            trade = pf.sell(
                self.state.portfolio, self.state.history,
                ticker=ticker, price=price, sell_ratio=ratio, reason=reason,
            )
            self.state.save()
            QMessageBox.information(
                self, "가상 매도 완료",
                f"{trade['name']} {trade['qty']}주\n"
                f"수익 {trade['pnl_krw']:+,.0f}원 ({trade['pnl_pct']:+.2f}%)",
            )
            self.sold.emit()
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "오류", str(e))


# ============================================================
#  History tab
# ============================================================

class HistoryTab(QWidget):
    def __init__(self, state) -> None:
        super().__init__()
        self.state = state
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        hdr = SectionHeader("거래 이력", "모든 매수·매도 기록")
        root.addWidget(hdr)

        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        root.addWidget(self._table, 1)
        self.refresh()

    def refresh(self) -> None:
        trades = self.state.history.get("trades", [])
        cols = ["날짜", "티커", "종목", "액션", "수량", "가격", "수익률", "손익", "사유"]
        self._table.setRowCount(len(trades))
        self._table.setColumnCount(len(cols))
        self._table.setHorizontalHeaderLabels(cols)

        for r, t in enumerate(reversed(trades)):
            action = t.get("action", "")
            d = t.get("exit_date") or t.get("entry_date", "")
            qty = t.get("qty", 0)
            price = t.get("price", 0)
            pnl_pct = t.get("pnl_pct", None)
            pnl_krw = t.get("pnl_krw", None)

            self._table.setItem(r, 0, QTableWidgetItem(str(d)))
            self._table.setItem(r, 1, QTableWidgetItem(t.get("ticker", "")))
            self._table.setItem(r, 2, QTableWidgetItem(t.get("name", "")))
            action_item = QTableWidgetItem("매수" if action == "buy" else "매도")
            if action == "buy":
                action_item.setForeground(QColor(COLORS["blue"]))
            else:
                action_item.setForeground(QColor(COLORS["red"] if (pnl_krw or 0) < 0 else COLORS["green"]))
            self._table.setItem(r, 3, action_item)
            self._table.setItem(r, 4, QTableWidgetItem(f"{qty:,}"))
            self._table.setItem(r, 5, QTableWidgetItem(f"{price:,.0f}"))
            if pnl_pct is not None:
                pnl_item = QTableWidgetItem(f"{pnl_pct:+.2f}%")
                pnl_item.setForeground(QColor(COLORS["green"] if pnl_pct >= 0 else COLORS["red"]))
                self._table.setItem(r, 6, pnl_item)
            if pnl_krw is not None:
                krw_item = QTableWidgetItem(f"{pnl_krw:+,.0f}")
                krw_item.setForeground(QColor(COLORS["green"] if pnl_krw >= 0 else COLORS["red"]))
                self._table.setItem(r, 7, krw_item)
            self._table.setItem(r, 8, QTableWidgetItem(t.get("reason", "")))

        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)


# ============================================================
#  Performance tab
# ============================================================

class PerformanceTab(QWidget):
    def __init__(self, state) -> None:
        super().__init__()
        self.state = state
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        hdr = SectionHeader("성과", "누적 실현손익 & 통계")
        root.addWidget(hdr)

        # KPI row
        self._kpi_row = QHBoxLayout()
        self._kpi_row.setSpacing(10)
        self._kpi_trades = MetricCard("TOTAL TRADES", "0")
        self._kpi_wins = MetricCard("WIN RATE", "-")
        self._kpi_avg = MetricCard("AVG RETURN", "-")
        self._kpi_pnl = MetricCard("REALIZED P/L", "0원")
        for w in (self._kpi_trades, self._kpi_wins, self._kpi_avg, self._kpi_pnl):
            self._kpi_row.addWidget(w)
        root.addLayout(self._kpi_row)

        # Chart
        self._plot = pg.PlotWidget()
        self._plot.setBackground(COLORS["bg_alt"])
        self._plot.showGrid(x=False, y=True, alpha=0.15)
        self._plot.getAxis("left").setTextPen(COLORS["text_dim"])
        self._plot.getAxis("bottom").setTextPen(COLORS["text_dim"])
        self._plot.setMinimumHeight(320)
        root.addWidget(self._plot, 1)
        self.refresh()

    def refresh(self) -> None:
        trades = self.state.history.get("trades", [])
        sells = [t for t in trades if t["action"] == "sell"]
        self._kpi_trades.set_value(str(len(sells)))

        if not sells:
            self._plot.clear()
            return

        df = pd.DataFrame(sells)
        df["exit_date"] = pd.to_datetime(df["exit_date"])
        df = df.sort_values("exit_date")
        df["cum_pnl"] = df["pnl_krw"].cumsum()

        wins = (df["pnl_krw"] > 0).sum()
        win_rate = wins / len(df) * 100
        avg_pct = df["pnl_pct"].mean()
        realized = df["pnl_krw"].sum()

        self._kpi_wins.set_value(f"{win_rate:.1f}%", "green" if win_rate >= 50 else "red")
        self._kpi_avg.set_value(f"{avg_pct:+.2f}%", "green" if avg_pct >= 0 else "red")
        self._kpi_pnl.set_value(f"{realized:+,.0f}원", "green" if realized >= 0 else "red")

        self._plot.clear()
        x = list(range(len(df)))
        y = df["cum_pnl"].tolist()
        pen = pg.mkPen(color=COLORS["green"] if realized >= 0 else COLORS["red"], width=2)
        self._plot.plot(x, y, pen=pen, symbol="o", symbolSize=6,
                        symbolBrush=COLORS["green"] if realized >= 0 else COLORS["red"])
        self._plot.setLabel("left", "누적 손익 (원)")
        self._plot.setLabel("bottom", "거래 #")


# ============================================================
#  Backtest tab (placeholder)
# ============================================================

class BacktestTab(QWidget):
    def __init__(self, state) -> None:
        super().__init__()
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(SectionHeader("백테스트", "Phase 2에서 구현 · 5년 데이터로 KOSPI 대비 알파 검증"))
        p = Panel("TODO")
        p.body().addWidget(QLabel(
            "백테스트는 Phase 2 작업입니다.\n\n"
            "목표:\n"
            "  · 2020~현재 5년 데이터로 walk-forward 시뮬\n"
            "  · 거래비용 0.3% / 슬리피지 0.1% 반영\n"
            "  · KOSPI 대비 알파, MDD, Sharpe, 승률 측정\n"
            "  · 통과 기준: 알파 +3% / MDD < 15% / Sharpe > 0.8 / 승률 > 50%"
        ))
        lay.addWidget(p)
        lay.addStretch()


# ============================================================
#  Settings tab
# ============================================================

class SettingsTab(QWidget):
    def __init__(self, state) -> None:
        super().__init__()
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(SectionHeader("설정", "config.yaml 요약 · 직접 편집 후 앱 재시작"))

        # Weights
        cfg = self.state.config
        f = cfg["scoring"]["factors"]
        strip = QHBoxLayout()
        strip.setSpacing(10)
        strip.addWidget(MetricCard("모멘텀 가중치", f"{f['momentum']}%", color="green"))
        strip.addWidget(MetricCard("수급 가중치",   f"{f['supply_demand']}%", color="gold"))
        strip.addWidget(MetricCard("퀄리티 가중치", f"{f['quality']}%"))
        strip.addWidget(MetricCard("역추세 가중치", f"{f['mean_reversion']}%"))
        lay.addLayout(strip)

        # Investment rules
        inv = Panel("투자금 룰 (점수 기준)")
        for rule in cfg["investment_rules"]:
            r = QLabel(f"·  총점 ≥ {rule['min_score']}점  →  {rule['amount_krw']:,}원")
            r.setStyleSheet(f"color:{COLORS['text']}; font-size:13px; padding:2px 0;")
            inv.body().addWidget(r)
        lay.addWidget(inv)

        # Sell rules
        sr = cfg["sell_rules"]
        sell_panel = Panel("매도 룰")
        for label, val in [
            ("하드 손절", f"{sr['hard_stop_loss_pct']}%"),
            ("부분 익절", f"+{sr['hard_take_profit_partial_pct']}% 에서 {int(sr['hard_take_profit_partial_ratio']*100)}% 청산"),
            ("타임 스톱", f"{sr['time_stop_days']} 거래일"),
            ("트레일링 스톱", f"{sr['trailing_stop_pct']}%"),
            ("매도점수 1차", f"{sr['sell_score_stage1']} 이상 → 50% 매도"),
            ("매도점수 2차", f"{sr['sell_score_stage2']} 이상 → 100% 매도"),
        ]:
            r = QLabel(f"·  {label}: {val}")
            r.setStyleSheet(f"color:{COLORS['text']}; font-size:13px; padding:2px 0;")
            sell_panel.body().addWidget(r)
        lay.addWidget(sell_panel)

        lay.addStretch()
