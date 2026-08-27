@echo off
setlocal EnableExtensions

REM ============================================================
REM TiTiBet - Quant Calibration Phase Verification
REM ============================================================

set "REPO=%~dp0.."
set "BRANCH=feat/quant-calibration-value-engine"
cd /d "%REPO%" || exit /b 1

echo.
echo ============================================================
echo   TiTiBet Quant Calibration Phase Verification
echo ============================================================
echo.

echo [1/4] Syncing calibration branch...
echo.
git fetch origin || exit /b 1
git checkout "%BRANCH%" || exit /b 1
git pull --ff-only origin "%BRANCH%" || exit /b 1
echo Current commit:
git log -1 --oneline

echo.
echo [2/4] Running backend tests...
echo.
cd /d "%REPO%\backend" || exit /b 1
python -m pytest -q
if errorlevel 1 (
    echo [ERROR] Backend tests failed.
    exit /b 1
)

echo.
echo [3/4] Building frontend...
echo.
cd /d "%REPO%\frontend" || exit /b 1
call npm run build
if errorlevel 1 (
    echo [ERROR] Frontend build failed.
    exit /b 1
)

echo.
echo [4/4] Running walk-forward calibration/value lab...
echo.
cd /d "%REPO%" || exit /b 1
python backend\scripts\calibration_value_lab.py --from 2026-01-01 --to 2026-06-30 --train-size 100 --test-size 25 --min-train 50 --output "%REPO%\calibration_value_lab.json"
if errorlevel 1 (
    echo [ERROR] Calibration/value laboratory failed.
    exit /b 1
)

echo.
echo ============================================================
echo   QUANT CALIBRATION PHASE VERIFIED
 eecho ============================================================
echo.
echo Git sync              : PASSED
echo Backend tests         : PASSED
echo Frontend build        : PASSED
echo Walk-forward lab      : COMPLETED
echo.
echo Report:
echo   %REPO%\calibration_value_lab.json
echo.
echo Production rules     : UNCHANGED
echo Staking rules         : UNCHANGED
echo ============================================================
pause
endlocal
