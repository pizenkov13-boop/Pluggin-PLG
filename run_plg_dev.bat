@echo off
setlocal
cd /d "%~dp0"

rem Development desktop mode: Vite hot-reloads the UI in the same native PLG
rem window and keeps the real pywebview ↔ Python bridge available.
set PYTHONIOENCODING=utf-8
set PLG_DEV=1
set PLG_DEV_URL=http://127.0.0.1:5173

if not exist "logs" mkdir logs

if not exist "web\node_modules\vite\bin\vite.js" (
  echo Installing the local web workspace first...
  pushd web
  call npm install
  if errorlevel 1 (
    popd
    echo Could not install the web workspace.
    pause
    exit /b 1
  )
  popd
)

start "PLG UI localhost" /min cmd /c "cd /d ""%~dp0web"" && npm run dev -- --host 127.0.0.1"
timeout /t 2 /nobreak >nul

pythonw "%~dp0PLG.pyw" --dev 1>>"%~dp0logs\plg_launch.log" 2>&1
