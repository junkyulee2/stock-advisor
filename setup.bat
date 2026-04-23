@echo off
REM Stock Advisor - first-time setup on a fresh PC.
REM Requires: Python 3.10+ on PATH.
setlocal
set PROJECT_DIR=%~dp0
cd /d "%PROJECT_DIR%"

echo.
echo === Stock Advisor setup ===
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [error] Python is not installed or not in PATH.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    echo During install, check "Add Python to PATH".
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%venv" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [error] venv creation failed.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Virtual environment already exists.
)

echo [2/3] Upgrading pip...
"%PROJECT_DIR%venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/3] Installing dependencies (may take 5-10 minutes)...
"%PROJECT_DIR%venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [error] dependency install failed.
    pause
    exit /b 1
)

echo.
echo === Setup complete! ===
echo Now run: launch.bat
echo Or run: scripts\make_shortcut.ps1  to create a desktop shortcut.
echo.
pause
