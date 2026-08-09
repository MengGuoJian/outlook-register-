@echo off
chcp 437 >nul
echo ========================================
echo   Outlook Registration Tool
echo ========================================
echo.

set PYTHON=D:\work\python.exe
set PROJECT_DIR=%~dp0OutlookRegister-main

if not exist "%PROJECT_DIR%\config.json" (
    echo [Error] config.json not found
    pause
    exit /b 1
)

echo [Info] Running registration...
echo.
pushd "%PROJECT_DIR%"
"%PYTHON%" main.py
popd

echo.
echo ========================================
echo   Done. Results saved in Results/
echo ========================================
echo.
echo Output files:
echo   - Results\outlook_token.txt
echo   - Results\logged_email.txt
echo   - Results\unlogged_email.txt
echo.
pause
