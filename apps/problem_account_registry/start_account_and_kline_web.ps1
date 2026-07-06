$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
& (Join-Path $Root.Path "scripts\start_all.ps1")
