@echo off
chcp 437 >nul
echo ========================================
echo   Get Token After Manual Verification
echo ========================================
echo.

set "PYTHON=D:\work\python.exe"
set "PROJECT_DIR=%~dp0OutlookRegister-main"

if not exist "%PROJECT_DIR%\config.json" (
    echo [Error] config.json not found
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%\Results\logged_email.txt" (
    echo [Error] No logged emails found. Run run.bat first to create emails.
    pause
    exit /b 1
)

echo [Info] Found registered emails in Results\logged_email.txt
echo.
pushd "%PROJECT_DIR%"
"%PYTHON%" get_token_one.py
popd

echo.
echo ========================================
echo   Done
echo ========================================
echo.
pause
