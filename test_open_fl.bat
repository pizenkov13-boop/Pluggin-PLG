@echo off
setlocal
cd /d "%~dp0"

if not exist "output_pattern.json" (
    echo No beat yet. Run PLG, click CREATE BEAT first.
    pause
    exit /b 1
)

echo Attaching starter sounds and generating PLG_Session.flp...
python fl_launch.py
if errorlevel 1 (
    echo fl_launch failed.
    pause
    exit /b 1
)

echo Done. FL should open with PLG Hi-Hats, PLG Sub 808, PLG Melody + starter wavs.
pause
