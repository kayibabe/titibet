@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM TiTiBet - Quant Phase Verification
REM Syncs the feature branch, runs backend tests, builds frontend,
REM then executes the quantitative benchmark.
REM ============================================================

set "REPO=%~dp0.."
set "SYNC=%REPO%\..\sync_titibet_quant_framework.bat"

cd /d "%REPO%" || exit /b 1

echo.
echo ============================================================
echo   TiTiBet Quant Phase Verification
echo ============================================================
echo.

echo [1/4] Syncing feature branch...
if exist "%SYNC%" (
    call "%SYNC%"
    if errorlevel 1 (
        echo [ERROR] Repository sync failed.
        exit /b 1
    )
) else (
    git fetch origin
    git checkout feat/quant-validation-framework
    git pull --ff-only origin feat/quant-validation-framework
    if errorlevel 1 exit /b 1
)

echo.
echo [2/4] Running backend tests...
cd /d "%REPO%\backend" || exit /b 1
python -m pytest -q
if errorlevel 1 (
    echo [ERROR] Backend tests failed.
    exit /b 1
)

echo.
echo [3/4] Building frontend...
cd /d "%REPO%\frontend" || exit /b 1
npm run build
if errorlevel 1 (
    echo [ERROR] Frontend build failed.
    exit /b 1
)

echo.
echo [4/4] Running quantitative benchmark...
cd /d "%REPO%" || exit /b 1
python backend\scripts\benchmark_engines.py --from 2026-01-01 --to 2026-06-30
if errorlevel 1 (
    echo [ERROR] Quantitative benchmark failed.
    exit /b 1
)

echo.
echo ============================================================
echo   QUANT PHASE VERIFIED SUCCESSFULLY
echo ============================================================
echo.
echo Backend tests  : PASSED
echo Frontend build : PASSED
echo Benchmark      : COMPLETED
echo Report:
echo   %REPO%\quant_engine_benchmark.json
echo.
pause
endlocal
