@echo off
setlocal EnableExtensions

REM ============================================================
REM TiTiBet - Quant Phase Verification
REM Sync branch, run tests/build, then run both quantitative reports.
REM ============================================================

set "REPO=%~dp0.."
set "BRANCH=feat/quant-validation-framework"
cd /d "%REPO%" || exit /b 1

echo.
echo ============================================================
echo   TiTiBet Quant Phase Verification
echo ============================================================
echo.

echo [1/5] Syncing feature branch...
echo.
git fetch origin || exit /b 1
git show-ref --verify --quiet "refs/heads/%BRANCH%"
if errorlevel 1 (
    git checkout -b "%BRANCH%" "origin/%BRANCH%" || exit /b 1
) else (
    git checkout "%BRANCH%" || exit /b 1
)
git pull --ff-only origin "%BRANCH%" || exit /b 1
echo Current commit:
git log -1 --oneline

echo.
echo [2/5] Running backend tests...
echo.
cd /d "%REPO%\backend" || exit /b 1
python -m pytest -q
if errorlevel 1 (
    echo [ERROR] Backend tests failed.
    exit /b 1
)
echo Backend tests completed successfully.

echo.
echo [3/5] Building frontend...
echo.
cd /d "%REPO%\frontend" || exit /b 1
call npm run build
if errorlevel 1 (
    echo [ERROR] Frontend build failed.
    exit /b 1
)
echo Frontend build completed successfully.

echo.
echo [4/5] Running strict gated quantitative benchmark...
echo.
cd /d "%REPO%" || exit /b 1
python backend\scripts\benchmark_engines.py --from 2026-01-01 --to 2026-06-30 --output "%REPO%\quant_engine_benchmark.json"
if errorlevel 1 (
    echo [ERROR] Strict quantitative benchmark failed.
    exit /b 1
)

echo.
echo [5/5] Running ungated model-quality laboratory (v2)...
echo.
python backend\scripts\model_quality_lab_v2.py --from 2026-01-01 --to 2026-06-30 --output "%REPO%\model_quality_lab.json"
if errorlevel 1 (
    echo [ERROR] Ungated model-quality laboratory failed.
    exit /b 1
)

echo.
echo ============================================================
echo   QUANT PHASE VERIFIED SUCCESSFULLY
echo ============================================================
echo.
echo Git sync              : PASSED
echo Backend tests         : PASSED
echo Frontend build        : PASSED
echo Strict benchmark      : COMPLETED
echo Ungated model lab     : COMPLETED
echo.
echo Reports:
echo   %REPO%\quant_engine_benchmark.json
echo   %REPO%\model_quality_lab.json
echo.
echo ============================================================
pause
endlocal
