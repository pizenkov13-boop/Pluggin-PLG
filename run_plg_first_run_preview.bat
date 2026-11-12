@echo off
setlocal
cd /d "%~dp0"

rem Local first-install preview: registration is shown first, then login,
rem password reset, account and billing can be checked in the browser.
if not exist "web\node_modules\vite\bin\vite.js" (
  echo Installing the local web workspace first...
  pushd web
  call npm install
  if errorlevel 1 (
    popd
    pause
    exit /b 1
  )
  popd
)

start "PLG first-run localhost" /min cmd /c "cd /d ""%~dp0web"" && npm run dev -- --host 127.0.0.1"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5173/?auth-preview"

