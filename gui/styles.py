"""QSS stylesheet — dark navy pro theme matching the desktop icon."""

COLORS = {
    "bg": "#0b1222",
    "bg_alt": "#0f172a",
    "bg_card": "#111c33",
    "bg_elev": "#162341",
    "border": "#22324f",
    "border_soft": "#1b2744",
    "text": "#e5edff",
    "text_dim": "#9aa8c7",
    "text_muted": "#6b7a9c",
    "green": "#22c55e",
    "green_dim": "#16a34a",
    "red": "#ef4444",
    "red_dim": "#b91c1c",
    "gold": "#fbbf24",
    "blue": "#3b82f6",
    "cyan": "#06b6d4",
    "purple": "#a855f7",
    "warn": "#f59e0b",
}


def main_qss() -> str:
    c = COLORS
    return f"""
QMainWindow, QWidget {{
    background-color: {c['bg']};
    color: {c['text']};
    font-family: "Segoe UI", "맑은 고딕", "Malgun Gothic", sans-serif;
    font-size: 13px;
}}

/* ---------- Header ---------- */
#Header {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0f172a, stop:1 #0b1222);
    border-bottom: 1px solid {c['border']};
}}
#HeaderTitle {{
    font-size: 20px;
    font-weight: 700;
    color: {c['text']};
}}
#HeaderSubtitle {{
    font-size: 11px;
    color: {c['text_muted']};
}}
#IconBadge {{
    background-color: {c['bg_elev']};
    border: 1px solid {c['border']};
    border-radius: 10px;
}}

/* ---------- Metric cards ---------- */
QFrame#MetricCard {{
    background-color: {c['bg_card']};
    border: 1px solid {c['border_soft']};
    border-radius: 10px;
    padding: 0px;
}}
QLabel#MetricLabel {{
    color: {c['text_muted']};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}}
QLabel#MetricValue {{
    color: {c['text']};
    font-size: 22px;
    font-weight: 700;
}}
QLabel#MetricValueGreen {{ color: {c['green']}; font-size: 22px; font-weight: 700; }}
QLabel#MetricValueRed   {{ color: {c['red']};   font-size: 22px; font-weight: 700; }}
QLabel#MetricValueGold  {{ color: {c['gold']};  font-size: 22px; font-weight: 700; }}
QLabel#MetricDelta {{
    font-size: 11px;
    color: {c['text_muted']};
}}

/* ---------- Tabs ---------- */
QTabWidget::pane {{
    border: 1px solid {c['border_soft']};
    border-radius: 10px;
    background-color: {c['bg_alt']};
    top: -1px;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {c['text_dim']};
    padding: 10px 18px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 600;
    font-size: 12px;
}}
QTabBar::tab:selected {{
    background-color: {c['bg_alt']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-bottom-color: {c['bg_alt']};
}}
QTabBar::tab:hover:!selected {{
    color: {c['text']};
    background-color: {c['bg_card']};
}}

/* ---------- Stock cards / panels ---------- */
QFrame#StockCard {{
    background-color: {c['bg_card']};
    border: 1px solid {c['border_soft']};
    border-radius: 10px;
}}
QFrame#StockCard:hover {{
    border: 1px solid {c['blue']};
}}
QFrame#Panel {{
    background-color: {c['bg_card']};
    border: 1px solid {c['border_soft']};
    border-radius: 10px;
}}
QFrame#PanelDark {{
    background-color: {c['bg_alt']};
    border: 1px solid {c['border_soft']};
    border-radius: 8px;
}}
QLabel#PanelTitle {{
    color: {c['text']};
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 0.3px;
}}
QLabel#StockName {{
    color: {c['text']};
    font-size: 15px;
    font-weight: 700;
}}
QLabel#StockTicker {{
    color: {c['text_muted']};
    font-family: "Consolas", monospace;
    font-size: 11px;
}}
QLabel#BigScore {{
    color: {c['gold']};
    font-size: 28px;
    font-weight: 800;
}}
QLabel#FactorLabel {{
    color: {c['text_muted']};
    font-size: 10px;
}}
QLabel#FactorValue {{
    color: {c['text']};
    font-size: 13px;
    font-weight: 700;
}}

/* ---------- Buttons ---------- */
QPushButton {{
    background-color: {c['bg_elev']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: #1c2a4d;
    border-color: {c['blue']};
}}
QPushButton:pressed {{
    background-color: #0d1833;
}}
QPushButton:disabled {{
    color: {c['text_muted']};
    background-color: #0c1528;
}}
QPushButton#PrimaryBtn {{
    background-color: {c['green']};
    color: #03160b;
    border: none;
}}
QPushButton#PrimaryBtn:hover {{ background-color: #2fd46a; }}
QPushButton#DangerBtn {{
    background-color: {c['red']};
    color: #1c0303;
    border: none;
}}
QPushButton#DangerBtn:hover {{ background-color: #f26464; }}
QPushButton#WarnBtn {{
    background-color: {c['gold']};
    color: #221600;
    border: none;
}}
QPushButton#WarnBtn:hover {{ background-color: #ffce4a; }}
QPushButton#GhostBtn {{
    background-color: transparent;
    color: {c['text_dim']};
    border: 1px solid {c['border']};
}}

/* ---------- Status pills ---------- */
QLabel#PillGreen {{
    background-color: rgba(34,197,94,0.15);
    color: {c['green']};
    border: 1px solid rgba(34,197,94,0.35);
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#PillRed {{
    background-color: rgba(239,68,68,0.15);
    color: {c['red']};
    border: 1px solid rgba(239,68,68,0.35);
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#PillGray {{
    background-color: rgba(107,122,156,0.15);
    color: {c['text_muted']};
    border: 1px solid rgba(107,122,156,0.35);
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#PillBlue {{
    background-color: rgba(59,130,246,0.15);
    color: {c['blue']};
    border: 1px solid rgba(59,130,246,0.35);
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}

/* ---------- Tables ---------- */
QTableWidget {{
    background-color: {c['bg_alt']};
    alternate-background-color: {c['bg_card']};
    color: {c['text']};
    gridline-color: {c['border_soft']};
    border: 1px solid {c['border_soft']};
    border-radius: 8px;
}}
QHeaderView::section {{
    background-color: {c['bg_elev']};
    color: {c['text_dim']};
    padding: 8px;
    border: none;
    border-right: 1px solid {c['border_soft']};
    font-weight: 700;
    font-size: 11px;
}}
QTableWidget::item {{
    padding: 6px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: rgba(59,130,246,0.2);
    color: {c['text']};
}}

/* ---------- ScrollArea ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: {c['bg_alt']};
    width: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {c['border']};
    min-height: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['blue']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* ---------- Log view ---------- */
QPlainTextEdit#LogView {{
    background-color: #060b18;
    color: #9aa8c7;
    border: 1px solid {c['border_soft']};
    border-radius: 8px;
    font-family: "Consolas", monospace;
    font-size: 11px;
}}

/* ---------- ComboBox / Spinbox ---------- */
QComboBox, QSpinBox {{
    background-color: {c['bg_elev']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 5px 10px;
}}
QComboBox::drop-down {{ border: none; }}
"""
