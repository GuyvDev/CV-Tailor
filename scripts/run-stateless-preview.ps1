param(
  [ValidateRange(1024, 65535)]
  [int]$Port = 3001
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$frontendDirectory = Join-Path $repositoryRoot "frontend"

$env:VITE_STATELESS_ONLY = "true"
Push-Location $frontendDirectory
try {
  npm run dev -- --port $Port
} finally {
  Pop-Location
}
