"""Entry point for the PyQt6 desktop app."""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Stock Advisor")
    app.setOrganizationName("HomeAI")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
