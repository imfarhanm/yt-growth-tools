@echo off
setlocal
cd /d "%~dp0"
title Handoff Check

python --version >nul 2>nul
if errorlevel 1 goto nopython

python -c "import PIL, numpy, imageio_ffmpeg" >nul 2>nul
if not errorlevel 1 goto ready
echo First run: installing what the tool needs. This takes 1-2 minutes...
python -m pip install --quiet --disable-pip-version-check pillow numpy imageio-ffmpeg
if errorlevel 1 goto nopip

:ready
set "THUMB=%~1"
set "VIDEO=%~2"
if defined THUMB if defined VIDEO goto run

echo.
echo  Handoff Check: does your video's first frame match your thumbnail?
echo.
echo  Drag a file into this window, then press Enter.
echo  Leave it empty and press Enter to try the sample.
echo.
set /p "THUMB=Thumbnail: "
if not defined THUMB goto sample
set "THUMB=%THUMB:"=%"
set /p "VIDEO=Video: "
if not defined VIDEO goto sample
set "VIDEO=%VIDEO:"=%"

:run
echo.
python handoff_check.py "%THUMB%" "%VIDEO%"
if errorlevel 1 goto failed
goto report

:sample
echo.
echo Running on the sample...
python handoff_check.py sample\thumb.png sample\video_before.mp4
if errorlevel 1 goto failed

:report
if not defined HANDOFF_NO_OPEN if exist handoff_report.png start "" handoff_report.png
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
