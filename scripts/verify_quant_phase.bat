@echo off
setlocal EnableExtensions

REM ============================================================
REM TiTiBet - Quant Phase Verification
REM Syncs the feature branch, runs backend tests, builds frontend,
REM then executes the strict Bayesian/Poisson/Dual benchmark.
REM ============================================================

set "REPO=%~dp0.."
set "SYNC=%~dp0..\..\sync_titibet_quant_framework.bat"

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
echo [4/4] Running strict quantitative benchmark...
cd /d "%REPO%" || exit /b 1
call "%REPO%\scripts\run_quant_benchmark.bat"
if errorlevel 1 (
    echo [ERROR] Quantitative benchmark failed.
    exit /b 1
)

echo.
echo ============================================================
echo   QUANT PHASE VERIFIED SUCCESSFULLY
echo ============================================================
echo.
echo Backend tests : PASSED
echo Frontend build : PASSED
echo Strict benchmark: COMPLETED
echo Report:
echo   %REPO%\quant_engine_benchmark.json
echo.
pause
endlocal
