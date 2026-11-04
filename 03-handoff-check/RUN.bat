@echo off
setlocal
cd /d "%~dp0"
title Handoff Check

python --version >nul 2>nul
if errorlevel 1 goto nopython

python -c "import PIL, numpy, imageio_ffmpeg" >nul 2>nul
if not errorlevel 1 goto ready
echo Pehli baar chal raha hai, zaroori cheezein install ho rahi hain. 1-2 minute lagenge...
python -m pip install --quiet --disable-pip-version-check pillow numpy imageio-ffmpeg
if errorlevel 1 goto nopip

:ready
set "THUMB=%~1"
set "VIDEO=%~2"
if defined THUMB if defined VIDEO goto run

echo.
echo  Handoff Check: thumbnail aur video ka pehla frame match karte hain ya nahi
echo.
echo  File ko is window mein kheench ke chhodo, phir Enter dabao.
echo  Dono khali chhod ke Enter dabaoge to sample pe chalega.
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
echo Sample pe chala raha hoon...
python handoff_check.py sample\thumb.png sample\video_before.mp4
if errorlevel 1 goto failed

:report
if not defined HANDOFF_NO_OPEN if exist handoff_report.png start "" handoff_report.png
goto end

:nopython
echo.
echo Python nahi mila. python.org/downloads se install karo.
echo Install karte waqt "Add python.exe to PATH" pe tick zaroor lagana, phir RUN.bat dobara chalao.
goto end

:nopip
echo.
echo Install nahi ho paya. Internet dekho, phir RUN.bat dobara chalao.
goto end

:failed
echo.
echo Upar error likha hai. README mein "Dikkat aaye to" dekho.

:end
echo.
pause
