"""Custom dialogs — buy amount picker, confirmations."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QRadioButton, QVBoxLayout, QWidget,
)

from .styles import COLORS


class BuyAmountDialog(QDialog):
    """Dialog to select buy amount for a recommended stock.

    Options: 10만 / 20만 / 30만 / custom. Default selection = score-tier auto.
    Shows resulting share count + actual cost for each option.
    """

    # Fixed tier options (KRW). Order matters for display.
    PRESETS = [100_000, 200_000, 300_000]

    def __init__(self, rec: dict, default_amount: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.rec = rec
        self.default_amount = default_amount if default_amount > 0 else self.PRESETS[0]
        self._selected_amount: Optional[int] = None
        self._selected_qty: Optional[int] = None
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        self.setWindowTitle("매수 금액 선택")
        self.setMinimumWidth(420)
        self.setStyleSheet(self._dialog_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        # ---------- header: stock info ----------
        name = self.rec.get("name", "-")
        ticker = self.rec.get("ticker", "")
        score = float(self.rec.get("total_score", 0))
        price = int(self.rec.get("close", 0))

        hdr = QLabel(f"<b>{name}</b>  <span style='color:{COLORS['text_muted']}'>({ticker})</span>")
        hdr.setStyleSheet("font-size:15px;")
        root.addWidget(hdr)

        sub = QLabel(
            f"총점 <b style='color:{COLORS['gold']}'>{score:.1f}</b>  ·  "
            f"현재가 <b>₩{price:,}</b>"
        )
        sub.setStyleSheet(f"color:{COLORS['text_dim']}; font-size:12px;")
        root.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background:{COLORS['border_soft']}; max-height:1px;")
        root.addWidget(sep)

        # ---------- options ----------
        self._btn_group = QButtonGroup(self)
        self._radios: list[QRadioButton] = []

        default_is_preset = self.default_amount in self.PRESETS

        for amount in self.PRESETS:
            qty, cost = self._calc_qty_cost(amount, price)
            is_default = amount == self.default_amount
            label_text = f"{amount:,}원"
            detail = f"→ {qty}주  ·  실매수 {cost:,}원"
            if cost > amount:
                detail = f"→ {qty}주  ·  <span style='color:{COLORS['gold']}'>실매수 {cost:,}원 (배정 초과)</span>"
            if is_default:
                label_text += "  (자동 추천)"

            rb = QRadioButton()
            rb.amount_value = amount   # attach attribute for retrieval
            self._btn_group.addButton(rb)
            self._radios.append(rb)

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            row.addWidget(rb)
            lbl = QLabel(
                f"<b>{label_text}</b>"
                f"<br><span style='color:{COLORS['text_muted']}; font-size:11px'>{detail}</span>"
            )
            lbl.setMinimumHeight(40)
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)

            wrap = QFrame()
            wrap.setObjectName("OptionRow")
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(10, 8, 10, 8)
            wl.addLayout(row)
            root.addWidget(wrap)

            # click label toggles radio too
            def _toggle_factory(r):
                def _toggle(event):
                    r.setChecked(True)
                return _toggle
            lbl.mousePressEvent = _toggle_factory(rb)

            if is_default:
                rb.setChecked(True)

        # ---------- custom ----------
        custom_row = QHBoxLayout()
        self._custom_rb = QRadioButton("커스텀")
        self._custom_rb.amount_value = -1
        self._btn_group.addButton(self._custom_rb)
        self._custom_input = QLineEdit()
        self._custom_input.setPlaceholderText("금액(원)")
        self._custom_input.setValidator(QIntValidator(10_000, 100_000_000))
        self._custom_input.setFixedWidth(140)
        self._custom_input.textChanged.connect(self._on_custom_changed)
        custom_row.addWidget(self._custom_rb)
        custom_row.addWidget(self._custom_input)
        self._custom_result = QLabel("")
        self._custom_result.setStyleSheet(f"color:{COLORS['text_muted']}; font-size:11px;")
        custom_row.addWidget(self._custom_result, 1)
        custom_wrap = QFrame()
        custom_wrap.setObjectName("OptionRow")
        cwl = QVBoxLayout(custom_wrap)
        cwl.setContentsMargins(10, 8, 10, 8)
        cwl.addLayout(custom_row)
        root.addWidget(custom_wrap)

        if not default_is_preset:
            self._custom_rb.setChecked(True)
            self._custom_input.setText(str(self.default_amount))

        root.addStretch()

        # ---------- buttons ----------
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("매수 실행")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ------------------------------------------------------------------
    def _calc_qty_cost(self, amount: int, price: int) -> tuple[int, int]:
        if price <= 0:
            return 0, 0
        qty = max(1, amount // price)
        return qty, qty * price

    def _on_custom_changed(self, text: str) -> None:
        self._custom_rb.setChecked(True)
        try:
            amt = int(text.replace(",", "").strip()) if text else 0
        except ValueError:
            amt = 0
        price = int(self.rec.get("close", 0))
        if amt > 0 and price > 0:
            qty, cost = self._calc_qty_cost(amt, price)
            msg = f"→ {qty}주  ·  실매수 {cost:,}원"
            if cost > amt:
                msg += "  (배정 초과)"
            self._custom_result.setText(msg)
        else:
            self._custom_result.setText("")

    def _on_accept(self) -> None:
        chosen = None
        for rb in self._radios:
            if rb.isChecked():
                chosen = rb.amount_value
                break
        if chosen is None and self._custom_rb.isChecked():
            try:
                chosen = int(self._custom_input.text().replace(",", "").strip())
            except ValueError:
                chosen = 0

        if not chosen or chosen <= 0:
            return  # stay open
        price = int(self.rec.get("close", 0))
        qty, cost = self._calc_qty_cost(chosen, price)
        self._selected_amount = chosen
        self._selected_qty = qty
        self._selected_cost = cost
        self.accept()

    # ------------------------------------------------------------------
    @property
    def chosen_amount(self) -> Optional[int]:
        return self._selected_amount

    @property
    def chosen_qty(self) -> Optional[int]:
        return self._selected_qty

    @property
    def chosen_cost(self) -> Optional[int]:
        return getattr(self, "_selected_cost", None)

    # ------------------------------------------------------------------
    def _dialog_qss(self) -> str:
        c = COLORS
        return f"""
QDialog {{ background-color: {c['bg']}; color: {c['text']}; }}
QLabel  {{ color: {c['text']}; font-size: 13px; }}
QFrame#OptionRow {{
    background-color: {c['bg_card']};
    border: 1px solid {c['border_soft']};
    border-radius: 8px;
}}
QFrame#OptionRow:hover {{ border-color: {c['blue']}; }}
QRadioButton {{ color: {c['text']}; font-size: 14px; spacing: 8px; }}
QLineEdit {{
    background-color: {c['bg_elev']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 5px;
    padding: 5px 8px;
}}
QDialogButtonBox QPushButton {{
    background-color: {c['bg_elev']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 6px 18px;
    min-width: 80px;
}}
QDialogButtonBox QPushButton[text=\"매수 실행\"] {{
    background-color: {c['green']};
    color: #031608;
    font-weight: 700;
    border: none;
}}
QDialogButtonBox QPushButton[text=\"매수 실행\"]:hover {{ background-color: #2fd46a; }}
"""
