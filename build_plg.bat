@echo off
setlocal
cd /d "%~dp0"

rem Modes:
rem   build_plg.bat release          the customer exe         -> dist\PLG.exe
rem   build_plg.bat tester [days]    the tester exe, local only, stops creating beats after <days> days
rem                                  (default 14; a negative number builds an already expired one, for checking)
rem                                                           -> dist\PLG_Tester.exe
rem The tester flavor is a build_flavor.json stamped into that exe alone (see PLG.spec); a release build clears
rem the variable below, never reads that file and fails if the finished exe carries it.
set BUILD_MODE=%1
set TESTER_DAYS=14
set PLG_BUILD_FLAVOR_FILE=
set OUT_EXE=PLG.exe
set VERIFY_MODE=release

if /I "%BUILD_MODE%"=="release" set PLG_BUILD_RELEASE=1
if /I "%BUILD_MODE%"=="tester" (
  set OUT_EXE=PLG_Tester.exe
  set VERIFY_MODE=tester
  if not "%~2"=="" set TESTER_DAYS=%~2
)

echo Building web UI...
pushd web
call npm run build
if errorlevel 1 (
  echo npm build failed.
  popd
  goto :fail
)
popd

echo Building %OUT_EXE% with bundled starter sounds...
python -c "from build_bundled_sounds import ensure_bundled_sounds; ensure_bundled_sounds()" || goto :fail
python -c "from starter_kit import ensure_starter_kit; ensure_starter_kit()" || goto :fail

rem App icon: assets\logo.ico is committed and PLG.spec picks it up directly.
rem The old `python scripts\make_icon.py` step was removed - scripts\ is not
rem part of this folder and the call aborted every release build.

where pyinstaller >nul 2>&1
if errorlevel 1 (
  echo Installing PyInstaller...
  pip install pyinstaller
)

if /I "%BUILD_MODE%"=="tester" goto :tester_flavor

rem The exe seeds its own .env from the bundled .env.release on first run
rem (app_config.seed_env_from_bundle), so nothing is copied to dist\ and nothing
rem is edited by hand. Fail the build if the template is not shippable.
python -c "import sys; from dotenv import dotenv_values; v = dotenv_values('.env.release'); bad = [k for k in ('PLG_CLOUD_MODE', 'PLG_CLOUD_URL', 'SUPABASE_URL', 'SUPABASE_ANON_KEY') if not (v.get(k) or '').strip()]; sys.exit('.env.release is missing values: ' + ', '.join(bad)) if bad else None" || goto :fail
pyinstaller --noconfirm PLG.spec
if errorlevel 1 goto :fail
goto :built

:tester_flavor
rem The tester exe ships .env.tester (no server, no keys) and never .env.release.
python tools\make_build_flavor.py %TESTER_DAYS% build\tester_flavor\build_flavor.json || goto :fail
set PLG_BUILD_FLAVOR_FILE=%CD%\build\tester_flavor\build_flavor.json
pyinstaller --noconfirm --workpath build\PLG_Tester PLG.spec
if errorlevel 1 goto :fail
set PLG_BUILD_FLAVOR_FILE=

:built
python tools\verify_build_flavor.py %VERIFY_MODE% dist\%OUT_EXE% || goto :fail

if /I "%BUILD_MODE%"=="tester" goto :done

if not defined PLG_SIGN_TIMESTAMP set PLG_SIGN_TIMESTAMP=http://timestamp.digicert.com
if defined PLG_SIGN_CERT (
  echo Signing with PLG_SIGN_CERT...
  signtool sign /fd SHA256 /tr "%PLG_SIGN_TIMESTAMP%" /td SHA256 /n "%PLG_SIGN_CERT%" "dist\PLG.exe"
) else if defined PLG_SIGN_THUMBPRINT (
  echo Signing with thumbprint...
  signtool sign /fd SHA256 /tr "%PLG_SIGN_TIMESTAMP%" /td SHA256 /sha1 "%PLG_SIGN_THUMBPRINT%" "dist\PLG.exe"
) else (
  echo Skipping code sign - set PLG_SIGN_CERT or PLG_SIGN_THUMBPRINT. See desktop\release-signing.md
)

:done
echo.
echo Done: dist\%OUT_EXE%
echo Starter sounds + web UI are inside the exe.
pause
exit /b 0

:fail
echo Build failed.
pause
exit /b 1
