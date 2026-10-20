@echo off
rem ============================================================
rem  pip_install.bat  |  pip install with China mirror fallback
rem
rem  Usage (call it from another .bat):
rem      call pip_install.bat -r requirements.txt
rem      call pip_install.bat --upgrade pip
rem      call pip_install.bat onnxruntime-directml
rem
rem  Behaviour:
rem   - tries Tsinghua / Aliyun / Tencent / USTC / official PyPI in order,
rem     returns 0 as soon as one of them works
rem   - fast fail per mirror (--timeout 15 --retries 1) so a dead mirror
rem     never looks like a frozen console
rem   - --prefer-binary: never build numpy / opencv from source on Windows
rem   - interpreter: %MOSAIC_PYTHON% if set, else .venv next to this script,
rem     else "python" from PATH
rem   - pin one mirror:  set MOSAIC_PIP_MIRROR=https://xxx/simple
rem ============================================================
setlocal EnableExtensions

if "%~1"=="" (
    echo [pip] nothing to install, usage: call pip_install.bat -r requirements.txt
    exit /b 2
)

set "PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
if defined MOSAIC_PYTHON set "PYTHON=%MOSAIC_PYTHON%"

rem avoid the extra PyPI version check round trip (it can hang on slow links)
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_NO_INPUT=1"

set "MIRRORS="
if defined MOSAIC_PIP_MIRROR set "MIRRORS=%MOSAIC_PIP_MIRROR% "
set "MIRRORS=%MIRRORS%https://pypi.tuna.tsinghua.edu.cn/simple"
set "MIRRORS=%MIRRORS% https://mirrors.aliyun.com/pypi/simple"
set "MIRRORS=%MIRRORS% https://mirrors.cloud.tencent.com/pypi/simple"
set "MIRRORS=%MIRRORS% https://pypi.mirrors.ustc.edu.cn/simple"
set "MIRRORS=%MIRRORS% https://pypi.org/simple"

for %%M in (%MIRRORS%) do (
    echo       [pip] source: %%M
    "%PYTHON%" -m pip install %* -i "%%M" --timeout 15 --retries 1 ^
        --prefer-binary --disable-pip-version-check --no-input
    if not errorlevel 1 (
        echo       [pip] done by %%M
        exit /b 0
    )
    echo       [pip] failed, trying next mirror ...
)

echo.
echo [pip] all mirrors failed. Things to try:
echo        1) pick a mirror manually:  set MOSAIC_PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
echo        2) behind a proxy:          set HTTP_PROXY=http://127.0.0.1:7890  set HTTPS_PROXY=http://127.0.0.1:7890
echo        3) offline wheels:          python -m pip install --no-index --find-links=.\wheels %*
echo        4) install by hand:         %PYTHON% -m pip install %* -i https://pypi.tuna.tsinghua.edu.cn/simple
exit /b 1
