@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo Python was not found. Please install Python 3.10+ from https://www.python.org/downloads/
    echo During install, make sure to check "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Setting up Kayock Support Desk for the first time. This may take a few minutes...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip >nul
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

echo.
echo Starting Kayock Support Desk...
echo.
python run.py

pause
