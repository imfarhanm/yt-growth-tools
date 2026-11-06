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
echo.
echo  Title Model Finder: which videos beat their channel's normal, and which title pattern did they use?
echo  To check thumbnails too, close this, put them in the thumbnails folder, and run it again.
echo.
if not defined TMF_NO_EDIT (
  echo  Notepad is opening channels.txt. Put your channels in, save, and close Notepad.
  start /wait notepad channels.txt
)
echo.
python title_model_finder.py channels.txt --ask --open
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
