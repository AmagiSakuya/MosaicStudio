@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] No virtualenv found. Run build.bat first, or create .venv manually.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main_gui.py %*
if errorlevel 1 pause
