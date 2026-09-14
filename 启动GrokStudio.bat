@echo off
cd /d "%~dp0"
title Grok Studio
echo.
echo Grok Studio
echo Folder: %cd%
echo.

set "PY="
if exist "D:\Python\3.11\python.exe" set "PY=D:\Python\3.11\python.exe"
if not defined PY (
  for /f "delims=" %%I in ('where python 2^>nul') do (
    echo %%I | findstr /i "WindowsApps LibreOffice" >nul
    if errorlevel 1 if not defined PY set "PY=%%I"
  )
)

if not defined PY (
  echo Python was not found. Install Python 3.11 and add it to PATH.
  echo.
  pause
  exit /b 1
)

echo Using: %PY%
"%PY%" --version
echo.

if not exist "grok_studio_server.py" (
  echo Cannot find grok_studio_server.py
  echo.
  pause
  exit /b 1
)

echo Keep this window open. Closing it stops the service.
echo Waiting until http://127.0.0.1:8787 is ready, then the browser will open.
echo.

start "Grok Studio Browser" cmd /c "powershell -NoProfile -ExecutionPolicy Bypass -Command \"for($i=0;$i -lt 90;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8787/__grok_health' -TimeoutSec 2; if($r.StatusCode -eq 200){ Start-Process 'http://127.0.0.1:8787'; exit 0 } } catch {} ; Start-Sleep -Seconds 1 }; Write-Host 'Server did not become ready in 90 seconds. Check the Grok Studio window.'; pause\""

"%PY%" grok_studio_server.py
echo.
echo Server exited. If the page did not open, read the error above.
pause
