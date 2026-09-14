@echo off
cd /d "%~dp0"
title Stop Grok Studio
echo.
echo Stopping process on port 8787 ...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Continue';" ^
  "$ids=@();" ^
  "try { Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction Stop | ForEach-Object { $ids += $_.OwningProcess } } catch {};" ^
  "Get-Process python,py -ErrorAction SilentlyContinue | ForEach-Object {" ^
  "  try {" ^
  "    $cl=(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id) -ErrorAction SilentlyContinue).CommandLine;" ^
  "    if ($cl -and $cl -match 'grok_studio_server.py') { $ids += $_.Id }" ^
  "  } catch {}" ^
  "};" ^
  "$ids=$ids | Where-Object { $_ -and $_ -gt 0 } | Select-Object -Unique;" ^
  "if (-not $ids) { Write-Host 'Nothing listening on 8787.'; exit 0 };" ^
  "foreach ($id in $ids) {" ^
  "  Write-Host ('Killing PID ' + $id);" ^
  "  cmd /c ('taskkill /F /PID ' + $id);" ^
  "};" ^
  "Start-Sleep -Seconds 1;" ^
  "$left=@(); try { $left=@(Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction Stop) } catch {};" ^
  "if ($left.Count -gt 0) { Write-Host 'Port 8787 is still open. Right-click this bat and Run as administrator.'; exit 1 } else { Write-Host 'Stopped.' }"

echo.
pause
