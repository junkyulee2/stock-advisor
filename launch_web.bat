@echo off
REM Stock Advisor v2.0 web app launcher (FastAPI + Uvicorn).
setlocal
set PROJECT_DIR=%~dp0
cd /d "%PROJECT_DIR%"
title ChunGyu Stock Advisor (web)

set PY="%PROJECT_DIR%venv\Scripts\python.exe"
if not exist %PY% (
    echo [error] venv not found. Run: python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

REM Open browser after a short delay
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765"

REM Run uvicorn in foreground (Ctrl+C to stop)
%PY% -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765 --reload
