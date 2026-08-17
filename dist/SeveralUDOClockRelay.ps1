$ErrorActionPreference = "Continue"
$relayRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pendingPath = Join-Path $relayRoot "pending_report.json"
$resultPath = Join-Path $relayRoot "last_result.json"
$mutex = New-Object System.Threading.Mutex($false, "SeveralUDOClockRelay")
if (-not $mutex.WaitOne(0, $false)) { exit 0 }

try {
    while ($true) {
        if (Test-Path -LiteralPath $pendingPath -PathType Leaf) {
            try {
                $envelope = Get-Content -LiteralPath $pendingPath -Raw | ConvertFrom-Json
                $headers = @{ Authorization = "Bearer $($envelope.sync_token)" }
                $body = $envelope.payload | ConvertTo-Json -Depth 12 -Compress
                $response = Invoke-WebRequest -Uri $envelope.receiver_url -Method Post -Headers $headers `
                    -ContentType "application/json" -Body $body -TimeoutSec 20
                $result = @{
                    ok = $true
                    status = [int]$response.StatusCode
                    sent_at = [DateTimeOffset]::UtcNow.ToString("o")
                } | ConvertTo-Json -Compress
                Set-Content -LiteralPath $resultPath -Value $result -Encoding UTF8
                Remove-Item -LiteralPath $pendingPath -Force
            }
            catch {
                $result = @{
                    ok = $false
                    error = $_.Exception.Message
                    attempted_at = [DateTimeOffset]::UtcNow.ToString("o")
                } | ConvertTo-Json -Compress
                Set-Content -LiteralPath $resultPath -Value $result -Encoding UTF8
            }
        }
        Start-Sleep -Seconds 2
    }
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
