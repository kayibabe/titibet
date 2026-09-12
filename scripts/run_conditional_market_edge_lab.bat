@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   TiTiBet Conditional Market Edge Lab
echo ============================================================
echo.

echo Research-only: production rules remain unchanged.
echo Discovery: 2026-01-01 through 2026-04-30
echo Validation: 2026-05-01 through 2026-06-30
echo.

python backend\scripts\conditional_market_edge_lab_safe_runner.py --from 2026-01-01 --to 2026-06-30 --validation-from 2026-05-01 --validation-to 2026-06-30 --output conditional_market_edge_lab.json
if errorlevel 1 (
    echo.
    echo [ERROR] Conditional market edge lab failed.
    exit /b 1
)

echo.
echo ============================================================
echo   CONDITIONAL LAB COMPLETE - PRODUCTION RULES UNCHANGED
echo ============================================================
echo Report: %CD%\conditional_market_edge_lab.json
exit /b 0
