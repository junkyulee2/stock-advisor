@echo off
REM Stock Advisor launcher (PyQt6 desktop app).
setlocal
set PROJECT_DIR=%~dp0
cd /d "%PROJECT_DIR%"
title Stock Advisor

set PY="%PROJECT_DIR%venv\Scripts\pythonw.exe"
set PY_CONSOLE="%PROJECT_DIR%venv\Scripts\python.exe"

if not exist %PY% (
    echo [error] venv not found. Run: python -m venv venv
    pause
    exit /b 1
)

REM Use pythonw to hide console. If crashes, run with python.exe to see errors.
start "" %PY% "%PROJECT_DIR%main_qt.py"
exit /b 0
