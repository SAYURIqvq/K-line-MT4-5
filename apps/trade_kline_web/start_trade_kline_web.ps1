$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Python = $env:K_DESK_PYTHON
if (-not $Python) {
    $Python = "C:\Users\amber\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
}

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

& $Python (Join-Path $PSScriptRoot "app.py")
