$ErrorActionPreference = "Stop"

$Port = 8765
$Url = "http://127.0.0.1:$Port/"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Python = $env:K_DESK_PYTHON
if (-not $Python) {
    $Python = "C:\Users\amber\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
}
$App = Join-Path $PSScriptRoot "app.py"
$Log = Join-Path $Root.Path "outputs\trade_kline_web.log"
$Err = Join-Path $Root.Path "outputs\trade_kline_web.err.log"

$env:K_DESK_ROOT = $Root.Path
$env:TRADE_KLINE_TOOL_DIR = Join-Path $Root.Path "tools\trade_kline_tool"
if (-not $env:TRADE_KLINE_OUT_DIR) {
    $legacyOut = "D:\risk\output_data"
    if (Test-Path (Split-Path -Parent $legacyOut)) {
        $env:TRADE_KLINE_OUT_DIR = $legacyOut
    } else {
        $env:TRADE_KLINE_OUT_DIR = Join-Path $Root.Path "outputs\kline"
    }
}
if (-not $env:TRADE_KLINE_PYDEPS) {
    $legacyPydeps = "D:\risk\pydeps"
    if (Test-Path $legacyPydeps) {
        $env:TRADE_KLINE_PYDEPS = $legacyPydeps
    } else {
        $env:TRADE_KLINE_PYDEPS = Join-Path $Root.Path "pydeps"
    }
}
New-Item -ItemType Directory -Force -Path $env:TRADE_KLINE_OUT_DIR | Out-Null

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    Start-Process -FilePath $Python `
        -ArgumentList @($App) `
        -WorkingDirectory $Root.Path `
        -WindowStyle Hidden `
        -RedirectStandardOutput $Log `
        -RedirectStandardError $Err
    Start-Sleep -Seconds 2
}

Start-Process $Url
Write-Host "Trade K-line web: $Url"
