@echo off
setlocal enabledelayedexpansion
title TiTiBet Shutdown
color 0E

echo ============================================================
echo                     TiTiBet Shutdown
echo ============================================================
echo.

set BACKEND_PORT=8010
set FRONTEND_PORT=5173
set /a KILLED=0

:: Must match the paths set in start-titibet.bat
set "BPID=%TEMP%\titibet-backend.pid"
set "FPID=%TEMP%\titibet-frontend.pid"

:: -- 1. Kill the backend server process by port --------------------
echo [1/3] Stopping backend  (port %BACKEND_PORT%)...
set /a BACKEND_KILLED=0
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":%BACKEND_PORT% " ^| findstr "LISTENING"') do (
    echo       Found PID %%a -- terminating.
    taskkill /F /PID %%a >nul 2>&1
    set /a KILLED+=1
    set /a BACKEND_KILLED+=1
)
if !BACKEND_KILLED! equ 0 echo       Nothing found on port %BACKEND_PORT%.

set /a FRONTEND_KILLED=0
echo [2/3] Stopping frontend (port %FRONTEND_PORT%)...
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":%FRONTEND_PORT% " ^| findstr "LISTENING"') do (
    echo       Found PID %%a -- terminating.
    taskkill /F /PID %%a >nul 2>&1
    set /a KILLED+=1
    set /a FRONTEND_KILLED+=1
)
if !FRONTEND_KILLED! equ 0 echo       Nothing found on port %FRONTEND_PORT%.

REM -- 2. Close the CMD console windows opened by start-titibet ------
REM
REM PRIMARY -- PID files written by start-titibet.bat at launch time.
REM This works even after Vite has renamed the "TiTiBet-Frontend" console title
REM to its own banner, which caused the old title-match to silently miss it.
REM
REM FALLBACK -- title-match for windows that somehow weren't captured in PID files
REM (e.g. backend window, which uvicorn never renames, may still show TiTiBet-*).
echo [3/3] Closing TiTiBet console windows...
set /a WINDOWS_CLOSED=0

if exist "%BPID%" (
    for /f "usebackq delims=" %%p in ("%BPID%") do (
        echo       Closing backend console  ^(PID %%p^)...
        taskkill /F /PID %%p >nul 2>&1
        set /a WINDOWS_CLOSED+=1
    )
    del "%BPID%" >nul 2>&1
) else (
    echo       No backend PID file found -- relying on fallback.
)

if exist "%FPID%" (
    for /f "usebackq delims=" %%p in ("%FPID%") do (
        echo       Closing frontend console ^(PID %%p^)...
        taskkill /F /PID %%p >nul 2>&1
        set /a WINDOWS_CLOSED+=1
    )
    del "%FPID%" >nul 2>&1
) else (
    echo       No frontend PID file found -- relying on fallback.
)

:: Fallback: catch any lingering TiTiBet-* titled windows
powershell -NoProfile -Command "$extra = 0; Get-Process -Name cmd -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'TiTiBet-*' } | ForEach-Object { Write-Host ('      Fallback close: ' + $_.MainWindowTitle); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue; $extra++ }; if ($extra -gt 0) { Write-Host ('      Closed ' + $extra + ' additional window(s) by title.') }"

:: -- Summary --------------------------------------------------------
echo.
if !KILLED! gtr 0 (
    echo [OK] Terminated !KILLED! server process^(es^) and closed !WINDOWS_CLOSED! console window^(s^).
) else (
    echo [INFO] No TiTiBet server processes were found running.
)
echo.
echo ============================================================
echo.
ping -n 3 127.0.0.1 >nul 2>&1
