# dev.ps1 - kills stale processes then launches backend + Vite in two windows
# Usage: .\dev.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host ""
Write-Host "  Job Apply - starting" -ForegroundColor Yellow
Write-Host "  --------------------"

# Free ports 8000 (backend) and 5173 (Vite) from any orphan process
foreach ($port in 8000, 5173) {
  $procIds = (Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
  foreach ($procId in $procIds) {
    if ($procId -and $procId -ne 0) {
      try {
        $p = Get-Process -Id $procId -ErrorAction Stop
        Write-Host "  killed orphan PID $procId ($($p.ProcessName)) on port $port" -ForegroundColor DarkGray
        Stop-Process -Id $procId -Force
      } catch {
        # process already gone
      }
    }
  }
}

# Brief pause so the OS releases the sockets
Start-Sleep -Milliseconds 400

# Backend FastAPI (port 8000) with auto-reload
Write-Host "  -> Backend  http://localhost:8000" -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
  "-NoExit",
  "-Command",
  "`$Host.UI.RawUI.WindowTitle='Job Apply Backend'; Set-Location '$root'; uvicorn backend.main:app --reload --port 8000"
)

# Extension Vite / CRXJS (port 5173)
Write-Host "  -> Vite     http://localhost:5173" -ForegroundColor Magenta
Start-Process powershell -ArgumentList @(
  "-NoExit",
  "-Command",
  "`$Host.UI.RawUI.WindowTitle='Job Apply Vite'; Set-Location '$root\extension'; npm run dev"
)

Write-Host ""
Write-Host "  Two windows launched. Ctrl+C in each to stop." -ForegroundColor DarkGray
Write-Host "  Reload the extension in chrome://extensions/ after manifest changes." -ForegroundColor DarkGray
Write-Host ""
