@echo off
setlocal EnableExtensions

REM ============================================================
REM TiTiBet - Quantitative Engine Benchmark
REM Runs Bayesian, Poisson and Dual on the same historical scope.
REM ============================================================

set "REPO=%~dp0.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
set "BACKEND=%REPO%\backend"
set "OUTPUT=%REPO%\quant_engine_benchmark.json"

cd /d "%REPO%" || (
    echo [ERROR] Could not access repository: %REPO%
    pause
    exit /b 1
)

set "FROM_DATE=%~1"
set "TO_DATE=%~2"
set "MARKET=%~3"

if "%FROM_DATE%"=="" set "FROM_DATE=2026-01-01"
if "%TO_DATE%"=="" set "TO_DATE=2026-06-30"

if not exist "%BACKEND%\scripts\benchmark_engines.py" (
    echo [ERROR] Benchmark script not found:
    echo         %BACKEND%\scripts\benchmark_engines.py
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   TiTiBet Quantitative Engine Benchmark
echo ============================================================
echo Repository : %REPO%
echo Date range : %FROM_DATE% to %TO_DATE%
if "%MARKET%"=="" echo Market     : ALL
echo.
echo Running Bayesian, Poisson and Dual...
echo.

cd /d "%BACKEND%" || (
    echo [ERROR] Could not access backend: %BACKEND%
    pause
    exit /b 1
)

if "%MARKET%"=="" (
    python scripts\benchmark_engines.py --from "%FROM_DATE%" --to "%TO_DATE%" --output "%OUTPUT%"
) else (
    echo Market     : %MARKET%
    python scripts\benchmark_engines.py --from "%FROM_DATE%" --to "%TO_DATE%" --market "%MARKET%" --output "%OUTPUT%"
)

if errorlevel 1 (
    echo.
    echo [ERROR] Quantitative benchmark failed.
    echo Check the Python output above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Benchmark completed successfully
 echo ============================================================
echo Report saved to:
echo   %OUTPUT%
echo.

if exist "%OUTPUT%" (
    echo Opening benchmark report...
    start "" "%OUTPUT%"
) else (
    echo [WARNING] JSON report was not found at the expected path.
)

echo.
pause
endlocal
