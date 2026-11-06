@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
cd /d "%~dp0"
title Title Model Finder

python --version >nul 2>nul
if errorlevel 1 goto nopython

python -c "import yt_dlp" >nul 2>nul
if not errorlevel 1 goto ready
echo First run: installing what the tool needs. This takes about a minute...
python -m pip install --quiet --disable-pip-version-check yt-dlp
if errorlevel 1 goto nopip

:ready
set "HASTHUMB="
for %%f in (thumbnails\*.png thumbnails\*.jpg thumbnails\*.jpeg thumbnails\*.webp) do set "HASTHUMB=1"
if not defined HASTHUMB goto go
python -c "import rapidocr_onnxruntime, PIL" >nul 2>nul
if not errorlevel 1 goto go
echo Thumbnails found: installing the text reader, once only. This takes 2-3 minutes...
python -m pip install --quiet --disable-pip-version-check rapidocr-onnxruntime pillow
if errorlevel 1 goto nopip

:go
python app.py
if errorlevel 1 goto failed
goto end

:nopython
echo.
echo Python was not found. Install it from python.org/downloads
echo During setup, tick "Add python.exe to PATH". Then run RUN.bat again.
goto end

:nopip
echo.
echo Setup failed. Check your internet connection, then run RUN.bat again.
goto end

:failed
echo.
echo The error is shown above. See "Troubleshooting" in the README.

:end
echo.
pause
