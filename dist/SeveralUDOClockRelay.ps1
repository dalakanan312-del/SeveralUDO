$ErrorActionPreference = "Continue"
$relayRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pendingPath = Join-Path $relayRoot "pending_report.json"
$resultPath = Join-Path $relayRoot "last_result.json"
$mutex = New-Object System.Threading.Mutex($false, "SeveralUDOClockRelay")
$ownsMutex = $false
try {
    $ownsMutex = $mutex.WaitOne(0, $false)
}
catch [System.Threading.AbandonedMutexException] {
    # A forcibly closed prior relay leaves an abandoned lock, but the new
    # process owns it and can safely continue.
    $ownsMutex = $true
}
if (-not $ownsMutex) { $mutex.Dispose(); exit 0 }

try {
    while ($true) {
        if (Test-Path -LiteralPath $pendingPath -PathType Leaf) {
            try {
                $envelope = Get-Content -LiteralPath $pendingPath -Raw | ConvertFrom-Json
                $headers = @{ Authorization = "Bearer $($envelope.sync_token)" }
                $body = $envelope.payload | ConvertTo-Json -Depth 12 -Compress
                # Invoke-RestMethod avoids Windows PowerShell 5.1's legacy
                # Internet Explorer engine, which can hang on headless PCs.
                $response = Invoke-RestMethod -Uri $envelope.receiver_url -Method Post -Headers $headers `
                    -ContentType "application/json" -Body $body -TimeoutSec 20
                $result = @{
                    ok = $true
                    status = 200
                    receiver_ok = [bool]$response.ok
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
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
