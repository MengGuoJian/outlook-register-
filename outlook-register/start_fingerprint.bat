@echo off
chcp 437 >nul
echo ========================================
echo   Fingerprint Browser Launcher
echo ========================================
echo.

set "CHROME=C:\Users\meng\AppData\Local\Chromium\Application\chrome.exe"
set "FINGERPRINT=1000"
set "PROFILE_DIR=D:\profiles\outlook_reg"
set "PROXY=http://127.0.0.1:7897"
set "PYTHON=D:\work\python.exe"
set "PROJECT_DIR=%~dp0OutlookRegister-main"

if not exist "%CHROME%" (
    echo [Error] Chrome not found: %CHROME%
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%\config.json" (
    echo [Error] config.json not found
    pause
    exit /b 1
)

echo [Info] Launching fingerprint browser...
echo        Fingerprint: %FINGERPRINT%
echo        Profile: %PROFILE_DIR%
echo        Proxy: %PROXY%
echo.

start "" "%CHROME%" --fingerprint=%FINGERPRINT% --user-data-dir="%PROFILE_DIR%" --proxy-server="%PROXY%" --lang=zh-CN

echo.
echo Browser launching in background...
echo Waiting 5 seconds before starting registration...
timeout /t 5 /nobreak >nul

echo.
echo ========================================
echo   Running Registration
echo ========================================
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
