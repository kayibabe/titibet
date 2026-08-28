@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   TiTiBet Conditional Market Coverage Audit
echo ============================================================
echo.
echo Research-only: production rules remain unchanged.
echo Scope: 2026-01-01 through 2026-06-30
echo Discovery: before 2026-05-01
echo Validation: 2026-05-01 through 2026-06-30
echo.

python backend\scripts\conditional_market_coverage_audit.py --from 2026-01-01 --to 2026-06-30 --validation-from 2026-05-01 --validation-to 2026-06-30 --output conditional_market_coverage_audit.json
if errorlevel 1 (
    echo.
    echo [ERROR] Conditional market coverage audit failed.
    exit /b 1
)

echo.
echo ============================================================
echo   COVERAGE AUDIT COMPLETE - PRODUCTION RULES UNCHANGED
echo ============================================================
echo Report: %CD%\conditional_market_coverage_audit.json
exit /b 0
