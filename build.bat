@echo off
setlocal
cd /d "%~dp0"
title Mosaic Studio - Build

echo ==========================================================
echo   Mosaic Studio  ^|  Build standalone app
echo ==========================================================
echo.

rem ---------- 0. locate python ----------
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (where py >nul 2>nul && set "PY=py -3")
if not defined PY (
    echo [ERROR] Python not found. Install Python 3.10+ and check "Add to PATH".
    goto :fail
)

rem ---------- 1. virtualenv ----------
if not exist ".venv\Scripts\python.exe" (
    echo [1/5] Creating virtualenv .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
) else (
    echo [1/5] Reusing existing virtualenv .venv
)

rem always talk to the venv interpreter directly - never rely on PATH / activate
set "MOSAIC_PYTHON=%CD%\.venv\Scripts\python.exe"

rem ---------- 2. dependencies ----------
echo [2/5] Checking dependencies ...
"%MOSAIC_PYTHON%" -c "import onnxruntime, cv2, numpy, PySide6, PySide6.QtMultimedia" >nul 2>nul
if errorlevel 1 (
    echo       Installing requirements.txt ...
    echo       ^(mirrors: tsinghua ^-^> aliyun ^-^> tencent ^-^> ustc ^-^> pypi.org^)
    call pip_install.bat -r requirements.txt
    if errorlevel 1 goto :pipfail
    "%MOSAIC_PYTHON%" -c "import onnxruntime, cv2, numpy, PySide6, PySide6.QtMultimedia" >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Packages installed but still cannot be imported.
        echo         Try: "%MOSAIC_PYTHON%" -m pip list
        goto :fail
    )
) else (
    echo       Already installed, skipped.
)
"%MOSAIC_PYTHON%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    call pip_install.bat pyinstaller
    if errorlevel 1 goto :pipfail
)

"%MOSAIC_PYTHON%" -c "import onnxruntime,sys; sys.stdout.write('      onnxruntime: '+onnxruntime.__version__+' ['+','.join(onnxruntime.get_available_providers())+']\n')"

rem ---------- 3. model (optional) ----------
rem 默认不把模型打进发布包：用户第一次运行时在「模型配置 → 齿轮 → 运行时管理」里下载
if /I "%BUNDLE_MODEL%"=="1" (
    if not exist "model\anime_rating\caformer_b36\model.onnx" (
        echo [3/5] Model not found, downloading ...
        "%MOSAIC_PYTHON%" download_model.py
        if errorlevel 1 goto :fail
    ) else (
        echo [3/5] Model OK
    )
) else (
    echo [3/5] Model NOT bundled - users download it inside the app ^(Runtime Manager^)
)

rem ---------- 4. ffmpeg ----------
if not exist "bin\ffmpeg.exe" (
    echo [4/5] ffmpeg not found, downloading ...
    "%MOSAIC_PYTHON%" download_ffmpeg.py
) else (
    echo [4/5] ffmpeg OK
)

rem ---------- 5. build ----------
rem PyInstaller --noconfirm wipes dist\MosaicStudio, which is where the models,
rem the CUDA runtime and the user's projects usually live. Park them aside first.
set "KEEP=dist\_keep"
if exist "dist\MosaicStudio\model" (
    if not exist "%KEEP%" mkdir "%KEEP%"
    move "dist\MosaicStudio\model" "%KEEP%\model" >nul 2>nul
)
if exist "dist\MosaicStudio\runtime" (
    if not exist "%KEEP%" mkdir "%KEEP%"
    move "dist\MosaicStudio\runtime" "%KEEP%\runtime" >nul 2>nul
)
if exist "dist\MosaicStudio\projects" (
    if not exist "%KEEP%" mkdir "%KEEP%"
    move "dist\MosaicStudio\projects" "%KEEP%\projects" >nul 2>nul
)

echo [5/5] Building with PyInstaller ...
"%MOSAIC_PYTHON%" -m PyInstaller --noconfirm --clean --windowed --onedir --name MosaicStudio ^
    --icon "src\gui\assets\app.ico" ^
    --add-data "src\gui\assets\app.ico;src\gui\assets" ^
    --collect-all onnxruntime --collect-all cv2 ^
    --exclude-module tkinter --exclude-module matplotlib ^
    --exclude-module scipy --exclude-module pandas --exclude-module PIL ^
    main_gui.py
if errorlevel 1 goto :fail

rem ---------- package dist ----------
echo       Preparing dist folder ...
if not exist "dist\MosaicStudio" mkdir "dist\MosaicStudio"
call :restore_keep

if /I "%BUNDLE_MODEL%"=="1" (
    xcopy "model" "dist\MosaicStudio\model" /E /I /Y >nul
) else (
    echo       model folder left as-is ^(not bundled from repo^)
)
xcopy "bin" "dist\MosaicStudio\bin" /E /I /Y >nul
if not exist "dist\MosaicStudio\projects" mkdir "dist\MosaicStudio\projects"
if exist "README.md" copy /Y "README.md" "dist\MosaicStudio\" >nul

rem ---------- optional: bundle CUDA runtime (~1.3 GB) ----------
if /I "%BUNDLE_CUDA%"=="1" (
    if exist ".venv\Lib\site-packages\nvidia" (
        echo       Bundling CUDA runtime into dist\MosaicStudio\cuda ...
        xcopy ".venv\Lib\site-packages\nvidia" "dist\MosaicStudio\cuda" /E /I /Y >nul
    ) else (
        echo       [WARN] nvidia packages not found in .venv, skipped CUDA bundle.
    )
)

echo.
echo ==========================================================
echo   Build OK
echo.
echo   dist\MosaicStudio\MosaicStudio.exe   ^<-- double click to run
echo.
echo   Projects go to  dist\MosaicStudio\projects\  (*.vproj)
echo   Media files can live anywhere - the project only stores references.
echo   Bundled ffmpeg  dist\MosaicStudio\bin\ffmpeg.exe
echo.
echo   Models and the CUDA runtime are NOT bundled by default:
echo   users download them inside the app (gear icon next to "Model settings").
echo   Set BUNDLE_MODEL=1 to ship the models, BUNDLE_CUDA=1 to ship CUDA DLLs.
echo.
echo   Rebuilding keeps what is already in dist\MosaicStudio:
echo     model\  runtime\  projects\   ^(parked in dist\_keep during the build^)
echo.
echo   The whole dist\MosaicStudio folder is portable.
echo ==========================================================
echo.
pause
exit /b 0

:fail
call :restore_keep
echo.
echo [FAILED] Build aborted. Check the messages above.
pause
exit /b 1

:pipfail
call :restore_keep
echo.
echo [FAILED] pip could not install the dependencies.
echo          Every mirror in pip_install.bat failed - check network / proxy, then either:
echo            set MOSAIC_PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
echo            or install by hand:  .venv\Scripts\python.exe -m pip install -r requirements.txt -i ^<mirror^>
pause
exit /b 1

rem ---------- helper: put back model / runtime / projects ----------
:restore_keep
if not exist "dist\_keep" exit /b 0
if not exist "dist\MosaicStudio" mkdir "dist\MosaicStudio"
if exist "dist\_keep\model" if not exist "dist\MosaicStudio\model" move "dist\_keep\model" "dist\MosaicStudio\model" >nul 2>nul
if exist "dist\_keep\runtime" if not exist "dist\MosaicStudio\runtime" move "dist\_keep\runtime" "dist\MosaicStudio\runtime" >nul 2>nul
if exist "dist\_keep\projects" if not exist "dist\MosaicStudio\projects" move "dist\_keep\projects" "dist\MosaicStudio\projects" >nul 2>nul
rd /S /Q "dist\_keep" >nul 2>nul
if exist "dist\_keep" (
    echo       [WARN] could not fully restore dist\_keep, check dist\_keep manually
) else (
    echo       restored model / runtime / projects from previous build
)
exit /b 0
