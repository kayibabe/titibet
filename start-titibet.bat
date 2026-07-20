@echo off
setlocal enabledelayedexpansion
title TiTiBet Launcher
color 0A

echo ============================================================
echo                      TiTiBet Launcher
echo ============================================================
echo.

:: PID file paths -- written here so stop-titibet.bat knows where to look.
:: Using %TEMP% avoids any drive/UNC issues with %~dp0 inside PowerShell strings.
set "BPID=%TEMP%\titibet-backend.pid"
set "FPID=%TEMP%\titibet-frontend.pid"

:: -- Locate Python --------------------------------------------------
set PYTHON=
if exist "C:\Python314\python.exe" set PYTHON=C:\Python314\python.exe
if not defined PYTHON (
    where python >nul 2>&1 && set PYTHON=python
)
if not defined PYTHON (
    where python3 >nul 2>&1 && set PYTHON=python3
)
if not defined PYTHON (
    echo [ERROR] Python not found. Install Python 3.10+ and add it to PATH.
    pause & exit /b 1
)
echo [OK] Python : %PYTHON%

:: -- Port conflict check --------------------------------------------
netstat -aon 2>nul | findstr ":8010 " | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [INFO] Port 8010 is already in use. Verifying whether it is TiTiBet...
    powershell -NoProfile -Command ^
      "$openapi = $null; " ^
      "try { $openapi = Invoke-RestMethod -UseBasicParsing http://localhost:8010/openapi.json -TimeoutSec 5 } catch { } " ^
      "if ($openapi -and $openapi.info -and $openapi.info.title -eq 'TiTiBet') { exit 0 } " ^
      "exit 1"
    if errorlevel 1 (
        echo [ERROR] Port 8010 is occupied by a different service.
        echo         Stop that service or change BACKEND_PORT before starting TiTiBet.
        pause & exit /b 1
    )
    echo [OK] Existing service on port 8010 is TiTiBet.
    echo.
)
netstat -aon 2>nul | findstr ":5173 " | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [WARN] Port 5173 already in use - frontend may already be running.
    echo.
)

:: -- Frontend dependencies ------------------------------------------
if not exist "%~dp0frontend\node_modules" (
    echo [INFO] node_modules missing -- running npm install first...
    cd /d "%~dp0frontend"
    npm.cmd install
    if errorlevel 1 (
        echo [ERROR] npm install failed. Fix the errors above and try again.
        pause & exit /b 1
    )
    echo.
)

:: -- Start backend --------------------------------------------------
echo [1/2] Starting backend...
cd /d "%~dp0backend"
netstat -aon 2>nul | findstr ":8010 " | findstr "LISTENING" >nul
if errorlevel 1 (
    start "TiTiBet-Backend" cmd /k "%PYTHON% run.py"
    REM Capture the new cmd window PID immediately, before the title can change.
    REM Note: | inside a double-quoted CMD string is literal -- no ^ needed here.
    powershell -NoProfile -Command "Start-Sleep -Milliseconds 400; $p = Get-Process cmd -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle -eq 'TiTiBet-Backend'} | Sort-Object StartTime -Descending | Select-Object -First 1; if ($p) { $p.Id | Out-File '%BPID%' -Encoding ascii -NoNewline; Write-Host '      Backend console PID' $p.Id 'saved.' }"
) else (
    echo       Reusing existing TiTiBet backend on port 8010.
)

:: Wait for TiTiBet backend identity instead of any generic 200 /health response.
echo       Waiting for TiTiBet backend on http://localhost:8010/openapi.json ...
powershell -NoProfile -Command ^
  "$deadline = (Get-Date).AddSeconds(60); " ^
  "while ((Get-Date) -lt $deadline) { " ^
  "  try { " ^
  "    $openapi = Invoke-RestMethod -UseBasicParsing http://localhost:8010/openapi.json -TimeoutSec 3; " ^
  "    if ($openapi.info.title -eq 'TiTiBet') { exit 0 } " ^
  "  } catch { } " ^
  "  Start-Sleep -Seconds 1; " ^
  "} " ^
  "exit 1"
if errorlevel 1 (
    echo [ERROR] TiTiBet backend did not become ready within 60 seconds.
    echo         Check the TiTiBet-Backend window for startup errors.
    pause & exit /b 1
)
echo       [OK] TiTiBet backend is ready.

:: -- Start frontend -------------------------------------------------
echo [2/2] Starting frontend...
cd /d "%~dp0frontend"
start "TiTiBet-Frontend" cmd /k "npm.cmd run dev"
REM Capture PID immediately -- Vite renames the console title to its own banner
REM within seconds, so title-matching at shutdown would miss this window.
powershell -NoProfile -Command "Start-Sleep -Milliseconds 400; $p = Get-Process cmd -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle -eq 'TiTiBet-Frontend'} | Sort-Object StartTime -Descending | Select-Object -First 1; if ($p) { $p.Id | Out-File '%FPID%' -Encoding ascii -NoNewline; Write-Host '      Frontend console PID' $p.Id 'saved.' }"

:: Poll until Vite is actually serving (max 30s) before opening browser.
echo       Waiting for TiTiBet frontend on http://localhost:5173 ...
powershell -NoProfile -Command ^
  "$deadline = (Get-Date).AddSeconds(30); " ^
  "while ((Get-Date) -lt $deadline) { " ^
  "  try { " ^
  "    $r = Invoke-WebRequest -UseBasicParsing http://localhost:5173 -TimeoutSec 2 -ErrorAction Stop; " ^
  "    if ($r.StatusCode -lt 500) { exit 0 } " ^
  "  } catch { } " ^
  "  Start-Sleep -Seconds 1; " ^
  "} " ^
  "exit 1"
if errorlevel 1 (
    echo [WARN] Frontend did not respond within 30 seconds -- opening browser anyway.
    echo        Check the TiTiBet-Frontend window for errors.
)
echo       [OK] TiTiBet frontend is ready.
start "" http://localhost:5173

echo.
echo ============================================================
echo   TiTiBet is running!
echo.
echo   Frontend : http://localhost:5173
echo   Backend  : http://localhost:8010
echo   API docs : http://localhost:8010/docs
echo   Health   : http://localhost:8010/health
echo.
echo   Run stop-titibet.bat to shut everything down.
echo ============================================================
echo.
pause
