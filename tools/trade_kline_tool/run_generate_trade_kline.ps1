param(
    [Parameter(Mandatory = $true)]
    [string]$Statement,

    [string]$OutDir = "",
    [string]$Terminal = "C:\Program Files\AC Capital Market MT5 Terminal\terminal64.exe",
    [int]$Mt5Timeout = 10000,
    [string]$Python = "C:\Users\amber\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-Path (Join-Path $ScriptDir "..\..")
if (-not $OutDir) {
    $legacyOut = "D:\risk\output_data"
    if (Test-Path (Split-Path -Parent $legacyOut)) {
        $OutDir = $legacyOut
    } else {
        $OutDir = Join-Path $Root.Path "outputs\kline"
    }
}
$env:K_DESK_ROOT = $Root.Path
$env:TRADE_KLINE_OUT_DIR = $OutDir
if (-not $env:TRADE_KLINE_PYDEPS) {
    $legacyPydeps = "D:\risk\pydeps"
    if (Test-Path $legacyPydeps) {
        $env:TRADE_KLINE_PYDEPS = $legacyPydeps
    } else {
        $env:TRADE_KLINE_PYDEPS = Join-Path $Root.Path "pydeps"
    }
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

& $Python `
    (Join-Path $ScriptDir "generate_trade_kline_from_statement.py") `
    $Statement `
    --out-dir $OutDir `
    --terminal $Terminal `
    --mt5-timeout $Mt5Timeout
