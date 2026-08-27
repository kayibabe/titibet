@echo off
setlocal EnableExtensions

REM ============================================================
REM TiTiBet - Quantitative Engine Benchmark
REM Runs Bayesian, Poisson and Dual on the same historical scope.
REM ============================================================

set "REPO=%~dp0.."
cd /d "%REPO%" || exit /b 1

set "FROM_DATE=%~1"
set "TO_DATE=%~2"
set "MARKET=%~3"

if "%FROM_DATE%"=="" set "FROM_DATE=2026-01-01"
if "%TO_DATE%"=="" set "TO_DATE=2026-06-30"

if "%MARKET%"=="" (
    python backend\scripts\benchmark_engines.py --from "%FROM_DATE%" --to "%TO_DATE%"
) else (
    python backend\scripts\benchmark_engines.py --from "%FROM_DATE%" --to "%TO_DATE%" --market "%MARKET%"
)

if errorlevel 1 (
    echo.
    echo [ERROR] Quantitative benchmark failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo Benchmark complete.
echo Report: %REPO%\quant_engine_benchmark.json
echo ============================================================
echo.
pause
endlocal
