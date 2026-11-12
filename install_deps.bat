@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 exit /b 1
python -m pytest -q
echo.
echo Installed. Optional extras: pip install -r requirements-optional.txt
pause
