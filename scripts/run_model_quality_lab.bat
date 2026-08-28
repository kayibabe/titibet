@echo off
setlocal EnableExtensions

REM ============================================================
REM TiTiBet - Ungated Model Quality Laboratory
REM Evaluates Bayesian / Poisson / 60:40 ensemble probabilities
REM independently of live signal gates.
REM ============================================================

set "REPO=%~dp0.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
set "BACKEND=%REPO%\backend"
set "OUTPUT=%REPO%\model_quality_lab.json"

cd /d "%REPO%" || exit /b 1

set "FROM_DATE=%~1"
set "TO_DATE=%~2"
set "MARKET=%~3"

if "%FROM_DATE%"=="" set "FROM_DATE=2026-01-01"
if "%TO_DATE%"=="" set "TO_DATE=2026-06-30"

if not exist "%BACKEND%\scripts\model_quality_lab.py" (
    echo [ERROR] Model quality lab not found:
    echo         %BACKEND%\scripts\model_quality_lab.py
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   TiTiBet Ungated Model Quality Laboratory
 echo ============================================================
echo Repository : %REPO%
echo Date range : %FROM_DATE% to %TO_DATE%
if "%MARKET%"=="" echo Market     : ALL
if not "%MARKET%"=="" echo Market     : %MARKET%
echo.

cd /d "%BACKEND%" || exit /b 1

if "%MARKET%"=="" (
    python scripts\model_quality_lab.py --from "%FROM_DATE%" --to "%TO_DATE%" --output "%OUTPUT%"
) else (
    python scripts\model_quality_lab.py --from "%FROM_DATE%" --to "%TO_DATE%" --market "%MARKET%" --output "%OUTPUT%"
)

if errorlevel 1 (
    echo.
    echo [ERROR] Model quality laboratory failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Model quality analysis completed
 echo ============================================================
echo Report:
echo   %OUTPUT%
echo.

if exist "%OUTPUT%" start "" "%OUTPUT%"

pause
endlocal
