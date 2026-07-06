$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Python = $env:K_DESK_PYTHON
if (-not $Python) {
    $Python = "C:\Users\amber\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
}

$env:K_DESK_ROOT = $Root.Path
$env:ACCOUNT_REGISTRY_DATA_DIR = Join-Path $Root.Path "local_data\problem_account_registry"
if (-not $env:TRADE_KLINE_OUT_DIR) {
    $legacyOut = "D:\risk\output_data"
    if (Test-Path (Split-Path -Parent $legacyOut)) {
        $env:TRADE_KLINE_OUT_DIR = $legacyOut
    } else {
        $env:TRADE_KLINE_OUT_DIR = Join-Path $Root.Path "outputs\kline"
    }
}
$env:TRADE_KLINE_WEB_URL = "http://127.0.0.1:8765"

New-Item -ItemType Directory -Force -Path $env:ACCOUNT_REGISTRY_DATA_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $env:TRADE_KLINE_OUT_DIR | Out-Null

& $Python (Join-Path $PSScriptRoot "app.py")
