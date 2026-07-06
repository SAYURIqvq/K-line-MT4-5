$checks = @(
    @{ Name = "Account Registry"; Url = "http://127.0.0.1:8776/api/accounts"; Port = 8776 },
    @{ Name = "Trade K-line Web"; Url = "http://127.0.0.1:8765/"; Port = 8765 }
)

foreach ($check in $checks) {
    $listener = Get-NetTCPConnection -LocalPort $check.Port -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) {
        Write-Host "$($check.Name): port $($check.Port) is not listening"
        continue
    }
    try {
        $response = Invoke-WebRequest -Uri $check.Url -UseBasicParsing -TimeoutSec 10
        Write-Host "$($check.Name): HTTP $($response.StatusCode)"
    } catch {
        Write-Host "$($check.Name): ERROR $($_.Exception.Message)"
    }
}

