@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting WebUI server...
python start_webui.py %*
