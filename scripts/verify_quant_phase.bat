@echo off
setlocal EnableExtensions

REM ============================================================
REM TiTiBet - Quant Phase Verification
REM Synchronizes the feature branch, runs backend tests, builds
REM frontend, then executes the quantitative benchmark.
REM This script is self-contained and does not depend on an
REM external sync .bat file.
REM ============================================================

set "REPO=%~dp0.."
set "BRANCH=feat/quant-validation-framework"

cd /d "%REPO%" || exit /b 1

echo.
echo ============================================================
echo   TiTiBet Quant Phase Verification
echo ============================================================
echo.

echo [1/4] Syncing feature branch...
echo.
git fetch origin
if errorlevel 1 (
    echo [ERROR] Git fetch failed.
    exit /b 1
)

git show-ref --verify --quiet "refs/heads/%BRANCH%"
if errorlevel 1 (
    echo Creating local feature branch from origin/%BRANCH%...
    git checkout -b "%BRANCH%" "origin/%BRANCH%"
) else (
    git checkout "%BRANCH%"
)
if errorlevel 1 (
    echo [ERROR] Could not switch to %BRANCH%.
    exit /b 1
)

git pull --ff-only origin "%BRANCH%"
if errorlevel 1 (
    echo [ERROR] Git pull failed.
    exit /b 1
)

echo.
echo Current commit:
git log -1 --oneline

echo.
echo [2/4] Running backend tests...
echo.
cd /d "%REPO%\backend" || exit /b 1
python -m pytest -q
if errorlevel 1 (
    echo.
    echo [ERROR] Backend tests failed.
    exit /b 1
)

echo.
echo [3/4] Building frontend...
echo.
cd /d "%REPO%\frontend" || exit /b 1
REM npm.cmd is itself a batch file on Windows, so CALL is required
REM here to return control to this verification script.
call npm run build
if errorlevel 1 (
    echo.
    echo [ERROR] Frontend build failed.
    exit /b 1
)

echo.
echo Frontend build completed successfully.
echo.
echo [4/4] Running quantitative benchmark...
echo.
cd /d "%REPO%" || exit /b 1
python backend\scripts\benchmark_engines.py --from 2026-01-01 --to 2026-06-30
if errorlevel 1 (
    echo.
    echo [ERROR] Quantitative benchmark failed.
    exit /b 1
)

echo.
echo ============================================================
echo   QUANT PHASE VERIFIED SUCCESSFULLY
echo ============================================================
echo.
echo Git sync       : PASSED
echo Backend tests  : PASSED
echo Frontend build : PASSED
echo Benchmark      : COMPLETED
echo.
echo Report:
echo   %REPO%\quant_engine_benchmark.json
echo.
echo ============================================================
pause
endlocal
