@echo off
setlocal EnableExtensions

REM ============================================================
REM TiTiBet - Quant Calibration & Value Research
REM Research-only: does not change production rules or staking.
REM ============================================================

set "REPO=%~dp0.."
cd /d "%REPO%" || exit /b 1

echo.
echo ============================================================
echo   TiTiBet Quant Calibration ^& Value Research
echo ============================================================
echo.

echo Running walk-forward calibration on the validated Jan-Jun 2026 scope...
echo.
python backend\scripts\calibration_value_lab.py --from 2026-01-01 --to 2026-06-30 --train-size 100 --test-size 25 --min-train 50 --output "%REPO%\calibration_value_lab.json"
if errorlevel 1 (
    echo.
    echo [ERROR] Calibration/value laboratory failed.
    exit /b 1
)

echo.
echo ============================================================
echo   CALIBRATION/VALUE LAB COMPLETED
 eecho ============================================================
echo.
echo Report:
echo   %REPO%\calibration_value_lab.json
echo.
echo NOTE: This report is research-only. No production gate or staking rule was changed.
echo ============================================================
pause
endlocal
