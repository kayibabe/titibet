@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   TiTiBet Conditional Market Window Audit
echo ============================================================
echo.
echo Research-only: production rules remain unchanged.
echo Scope: 2026-01-01 through 2026-06-30
echo Minimum discovery fixtures with snapshots: 100
echo Minimum validation fixtures with snapshots: 50
echo.

python backend\scripts\conditional_market_window_audit.py --from 2026-01-01 --to 2026-06-30 --min-discovery 100 --min-validation 50 --output conditional_market_window_audit.json
if errorlevel 1 (
    echo.
    echo [ERROR] Conditional market window audit failed.
    exit /b 1
)

echo.
echo ============================================================
echo   WINDOW AUDIT COMPLETE - PRODUCTION RULES UNCHANGED
echo ============================================================
echo Report: %CD%\conditional_market_window_audit.json
exit /b 0
