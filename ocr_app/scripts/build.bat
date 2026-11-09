@echo off
REM Build des workers OCR avec PyInstaller (mode --onedir, R3)
REM Usage: scripts\build.bat [simple|medium|advanced]
setlocal
if "%~1"=="" set WORKER=simple
if not "%~1"=="" set WORKER=%~1
python scripts\build.py --worker %WORKER%
endlocal