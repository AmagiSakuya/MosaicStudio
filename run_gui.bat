@echo off
setlocal
cd /d "%~dp0"

rem ---------- 1. interpreter ----------
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] No virtualenv at .venv
    echo        Run build.bat once - it creates .venv and installs everything,
    echo        or do it by hand:
    echo            python -m venv .venv
    echo            call pip_install.bat -r requirements.txt
    echo.
    pause
    exit /b 1
)
set "MOSAIC_PYTHON=%CD%\.venv\Scripts\python.exe"

rem ---------- 2. dependencies (with China mirror fallback) ----------
".venv\Scripts\python.exe" -c "import onnxruntime, cv2, numpy, PySide6, PySide6.QtMultimedia" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Missing packages, installing requirements.txt ...
    echo       ^(mirrors: tsinghua ^-^> aliyun ^-^> tencent ^-^> ustc ^-^> pypi.org^)
    call pip_install.bat -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] Could not install the dependencies. See the hints above.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -c "import onnxruntime, cv2, numpy, PySide6, PySide6.QtMultimedia" >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [ERROR] Packages installed but still cannot be imported.
        echo         Check with: .venv\Scripts\python.exe -m pip list
        pause
        exit /b 1
    )
)

rem ---------- 3. run ----------
".venv\Scripts\python.exe" main_gui.py %*
if errorlevel 1 pause
