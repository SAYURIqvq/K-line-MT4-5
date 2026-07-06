param(
    [Parameter(Mandatory = $true)]
    [string]$Trades,

    [string]$Mapping = "",
    [string]$Out = "",
    [string]$Python = "C:\Users\amber\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArgsList = @(
    (Join-Path $ScriptDir "build_enhanced_trade_kline_from_cache.py"),
    "--trades",
    $Trades
)

if ($Mapping) {
    $ArgsList += @("--mapping", $Mapping)
}
if ($Out) {
    $ArgsList += @("--out", $Out)
}

& $Python @ArgsList
