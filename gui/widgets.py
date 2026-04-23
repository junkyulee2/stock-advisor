"""Reusable Qt widgets: MetricCard, StockCard, Pill, SectionTitle, etc."""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QSpacerItem, QVBoxLayout, QWidget,
)


class MetricCard(QFrame):
    def __init__(self, label: str, value: str, *, color: str = "neutral", delta: str = "") -> None:
        super().__init__()
        self.setObjectName("MetricCard")
        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(2)

        self._label = QLabel(label.upper())
        self._label.setObjectName("MetricLabel")
        self._value = QLabel(value)
        self._set_color(color)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._value)
        if delta:
            self._delta = QLabel(delta)
            self._delta.setObjectName("MetricDelta")
            self._delta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(self._delta)
        else:
            self._delta = None

        lay.addWidget(self._label)
        lay.addLayout(row)

    def _set_color(self, color: str) -> None:
        mapping = {
            "neutral": "MetricValue",
            "green": "MetricValueGreen",
            "red": "MetricValueRed",
            "gold": "MetricValueGold",
        }
        self._value.setObjectName(mapping.get(color, "MetricValue"))

    def set_value(self, value: str, color: str = "neutral") -> None:
        self._value.setText(value)
        self._set_color(color)
        self._value.style().unpolish(self._value)
        self._value.style().polish(self._value)


class Pill(QLabel):
    def __init__(self, text: str, variant: str = "gray") -> None:
        super().__init__(text)
        mapping = {
            "green": "PillGreen",
            "red": "PillRed",
            "gray": "PillGray",
            "blue": "PillBlue",
        }
        self.setObjectName(mapping.get(variant, "PillGray"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(22)


class Panel(QFrame):
    def __init__(self, title: str = "", dark: bool = False) -> None:
        super().__init__()
        self.setObjectName("PanelDark" if dark else "Panel")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(14, 12, 14, 12)
        self._lay.setSpacing(8)
        if title:
            t = QLabel(title)
            t.setObjectName("PanelTitle")
            self._lay.addWidget(t)

    def body(self) -> QVBoxLayout:
        return self._lay


class StockCard(QFrame):
    buy_clicked = pyqtSignal(dict)

    def __init__(self, rec: dict, held: bool = False) -> None:
        super().__init__()
        self.rec = rec
        self.setObjectName("StockCard")
        self.setMinimumHeight(110)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(20)

        # Left: name + ticker
        left = QVBoxLayout()
        left.setSpacing(2)
        name = QLabel(rec.get("name", "-"))
        name.setObjectName("StockName")
        ticker = QLabel(f"{rec.get('ticker','')} · {rec.get('market','')}")
        ticker.setObjectName("StockTicker")
        left.addWidget(name)
        left.addWidget(ticker)
        left.addStretch()
        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setMinimumWidth(180)
        outer.addWidget(left_w, 2)

        # Middle: score big
        score_box = QVBoxLayout()
        score_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_lbl = QLabel("SCORE")
        score_lbl.setObjectName("MetricLabel")
        score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_val = QLabel(f"{rec.get('total_score',0):.1f}")
        score_val.setObjectName("BigScore")
        score_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_box.addWidget(score_lbl)
        score_box.addWidget(score_val)
        sw = QWidget()
        sw.setLayout(score_box)
        sw.setFixedWidth(110)
        outer.addWidget(sw)

        # Middle 2: factor breakdown
        fgrid = QVBoxLayout()
        fgrid.setSpacing(4)
        factors = [
            ("모멘텀", rec.get("momentum_score", 0), "#22c55e"),
            ("수급",   rec.get("supply_demand_score", 0), "#3b82f6"),
            ("퀄리티", rec.get("quality_score", 0), "#a855f7"),
            ("역추세", rec.get("mean_reversion_score", 0), "#fbbf24"),
        ]
        for fname, fval, fcolor in factors:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            nm = QLabel(fname)
            nm.setObjectName("FactorLabel")
            nm.setFixedWidth(46)
            bar = _MiniBar(float(fval or 0), fcolor)
            vv = QLabel(f"{float(fval or 0):.0f}")
            vv.setObjectName("FactorValue")
            vv.setFixedWidth(28)
            vv.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(nm)
            row.addWidget(bar, 1)
            row.addWidget(vv)
            fgrid.addLayout(row)
        fw = QWidget()
        fw.setLayout(fgrid)
        outer.addWidget(fw, 3)

        # Right: price + amount + buy button
        right = QVBoxLayout()
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        stock_price = int(rec.get("close", 0))
        price = QLabel(f"₩{stock_price:,}")
        price.setObjectName("StockName")
        price.setAlignment(Qt.AlignmentFlag.AlignRight)
        amount = int(rec.get("amount_krw", 0))

        # Determine actual share count and cost
        if amount > 0 and stock_price > 0:
            target_qty = amount // stock_price
            actual_qty = max(1, target_qty)
            actual_cost = actual_qty * stock_price
            over_budget = actual_cost > amount
        else:
            actual_qty = 0
            actual_cost = 0
            over_budget = False

        if amount > 0 and actual_qty > 0:
            amt_text = f"배정 {amount:,}원"
            if over_budget:
                amt_text += f"  →  1주 실매수 {actual_cost:,}원"
        else:
            amt_text = "85점 미달"
        amt_lbl = QLabel(amt_text)
        amt_lbl.setObjectName("StockTicker")
        amt_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        if over_budget:
            amt_lbl.setStyleSheet("color: #fbbf24;")  # gold tint to flag over-budget

        right.addWidget(price)
        right.addWidget(amt_lbl)

        if held:
            pill = Pill("보유 중", "blue")
            right.addWidget(pill, 0, Qt.AlignmentFlag.AlignRight)
        elif amount > 0 and actual_qty > 0:
            label = f"{actual_qty}주 매수 ({actual_cost:,}원)"
            btn = QPushButton(label)
            btn.setObjectName("PrimaryBtn")
            btn.setFixedHeight(32)
            btn.setMinimumWidth(170)
            btn.clicked.connect(lambda: self.buy_clicked.emit(self.rec))
            right.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)
        rw = QWidget()
        rw.setLayout(right)
        rw.setMinimumWidth(140)
        outer.addWidget(rw, 2)


class _MiniBar(QFrame):
    """Horizontal gauge 0..100 with color fill."""
    def __init__(self, value: float, color: str) -> None:
        super().__init__()
        self._v = max(0.0, min(100.0, float(value)))
        self._color = color
        self.setFixedHeight(6)
        self.setMinimumWidth(60)
        self.setStyleSheet("background-color: #1b2744; border-radius: 3px;")
        self._fill = QFrame(self)
        self._fill.setStyleSheet(f"background-color: {color}; border-radius: 3px;")
        self._fill.setGeometry(0, 0, 0, 6)

    def resizeEvent(self, event):
        w = int(self.width() * (self._v / 100.0))
        self._fill.setGeometry(0, 0, w, self.height())
        return super().resizeEvent(event)


class SectionHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("HeaderTitle")
        lay.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("HeaderSubtitle")
            lay.addWidget(s)
