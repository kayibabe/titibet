@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   TiTiBet Q2 Market Edge Discovery ^& Eligibility
 echo ============================================================
echo.

if not exist "model_quality_lab.json" (
    echo [ERROR] model_quality_lab.json not found.
    echo Run scripts\verify_quant_phase.bat first.
    exit /b 1
)

python backend\scripts\market_edge_discovery_v2.py --input model_quality_lab.json --output market_edge_discovery.json
if errorlevel 1 (
    echo.
    echo [ERROR] Q2 market edge discovery failed.
    exit /b 1
)

echo.
echo ============================================================
echo   Q2 COMPLETE - PRODUCTION RULES UNCHANGED
echo ============================================================
echo Report: %CD%\market_edge_discovery.json
exit /b 0
