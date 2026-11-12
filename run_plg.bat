@echo off
setlocal
cd /d "%~dp0"

rem Log lines contain "->" arrows and Russian text. Without this the console
rem codepage (cp1251 here) makes logging raise UnicodeEncodeError mid-run.
set PYTHONIOENCODING=utf-8

if not exist "web\dist\index.html" (
  echo Building web UI...
  pushd web
  call npm run build
  if errorlevel 1 (
    echo npm build failed — install Node.js and run: cd web ^&^& npm install ^&^& npm run build
    popd
    goto :fallback
  )
  popd
)

if not exist "logs" mkdir logs

pythonw "%~dp0PLG.pyw" 1>>"%~dp0logs\plg_launch.log" 2>&1
if not errorlevel 1 exit /b 0

echo PLG webview could not start. See logs\plg_launch.log
type "%~dp0logs\plg_launch.log" | more +0

:fallback
echo Trying legacy tkinter UI...
python "%~dp0plg_app.py" 1>>"%~dp0logs\plg_launch.log" 2>&1
if errorlevel 1 (
  echo.
  echo If cloud mode is on, start API: python cloud\run.py
  echo Or set PLG_CLOUD_MODE=false in .env for local Gemini.
  echo Or set PLG_CLOUD_URL=https://cloud.example.com
)
pause
