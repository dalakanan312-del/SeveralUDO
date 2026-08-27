param(
    [switch]$SelfTest,
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$relayRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$queuePath = Join-Path $relayRoot "report_queue"
$quarantinePath = Join-Path $relayRoot "report_quarantine"
$legacyPendingPath = Join-Path $relayRoot "pending_report.json"
$resultPath = Join-Path $relayRoot "last_result.json"
$healthPath = Join-Path $relayRoot "relay_health.json"
$configPath = Join-Path $relayRoot "config.json"
$mutex = New-Object System.Threading.Mutex($false, "SeveralUDOClockRelay22")
$ownsMutex = $false

function Write-JsonAtomic {
    param([string]$Path, [object]$Value)
    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 20 -Compress | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Read-JsonFile {
    param([string]$Path)
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Write-RelayHealth {
    param([string]$State, [string]$Message = "", [object]$Envelope = $null)
    $queued = @(Get-ChildItem -LiteralPath $queuePath -Filter "report-*.json" -File -ErrorAction SilentlyContinue).Count
    $quarantined = @(Get-ChildItem -LiteralPath $quarantinePath -Filter "*.json" -File -ErrorAction SilentlyContinue).Count
    $value = @{
        relay_version = "2.2.7"
        state = $State
        message = $Message
        queue_depth = $queued
        quarantined = $quarantined
        checked_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    if ($null -ne $Envelope) {
        $value.report_sequence = $Envelope.report_sequence
        $value.report_checksum = $Envelope.report_checksum
    }
    Write-JsonAtomic -Path $healthPath -Value $value
}

function Test-ClockSyncInstall {
    $checks = [ordered]@{}
    $checks.config_present = Test-Path -LiteralPath $configPath -PathType Leaf
    $checks.script_mod_present = Test-Path -LiteralPath (Join-Path $relayRoot "SeveralUDOClockSync.ts4script") -PathType Leaf
    $checks.relay_present = Test-Path -LiteralPath $PSCommandPath -PathType Leaf
    $checks.queue_writable = $false
    $checks.configuration_valid = $false
    $checks.receiver_reachable = $false
    $checks.message = ""
    try {
        New-Item -ItemType Directory -Path $queuePath -Force | Out-Null
        $probe = Join-Path $queuePath ".write-test"
        Set-Content -LiteralPath $probe -Value "ok" -Encoding ASCII
        Remove-Item -LiteralPath $probe -Force
        $checks.queue_writable = $true
        if ($checks.config_present) {
            $config = Read-JsonFile $configPath
            $checks.configuration_valid = [bool]($config.receiver_url -and $config.sync_token -and $config.enabled -ne $false)
            if ($checks.configuration_valid) {
                $pingUrl = ([string]$config.receiver_url) -replace '/report$', '/ping'
                $headers = @{ Authorization = "Bearer $($config.sync_token)" }
                try {
                    $ping = Invoke-RestMethod -Uri $pingUrl -Method Get -Headers $headers -TimeoutSec 15
                    $checks.receiver_reachable = [bool]$ping.ok
                    $checks.receiver = $ping
                }
                catch {
                    # Clock Sync 2.1 receivers predate the non-mutating ping route.  A
                    # 404/405 proves that the configured tracker host is reachable; the
                    # relay can continue using its existing report endpoint until the
                    # tracker is upgraded.  Authentication and protocol compatibility
                    # are still enforced when an actual queued report is delivered.
                    $statusCode = 0
                    if ($null -ne $_.Exception.Response) {
                        $statusCode = [int]$_.Exception.Response.StatusCode
                    }
                    if ($statusCode -in @(404, 405)) {
                        $priorAccepted = $false
                        if (Test-Path -LiteralPath $resultPath -PathType Leaf) {
                            $priorResult = Read-JsonFile $resultPath
                            $priorStatus = [int]$priorResult.status
                            $priorAccepted = $priorStatus -ge 200 -and $priorStatus -lt 300
                        }
                        if ($priorAccepted) {
                            $checks.receiver_reachable = $true
                            $checks.receiver = @{
                                ok = $true
                                mode = "legacy-compatible"
                                warning = "The tracker accepted a prior report but does not expose the Clock Sync 2.2 ping route yet."
                            }
                        }
                        else {
                            $checks.message = "The tracker is reachable but its Clock Sync 2.2 ping route is not deployed, and no prior accepted report could verify this private link."
                        }
                    }
                    else { throw }
                }
            }
        }
    }
    catch {
        $checks.message = $_.Exception.Message
    }
    $checks.ok = [bool]($checks.config_present -and $checks.script_mod_present -and $checks.relay_present -and $checks.queue_writable -and $checks.configuration_valid -and $checks.receiver_reachable)
    $checks.checked_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-JsonAtomic -Path (Join-Path $relayRoot "self_test_result.json") -Value $checks
    return $checks.ok
}

function Import-LegacyPendingReport {
    if (-not (Test-Path -LiteralPath $legacyPendingPath -PathType Leaf)) { return }
    try {
        $legacy = Read-JsonFile $legacyPendingPath
        $sequence = 0
        if ($legacy.report_sequence) { $sequence = [long]$legacy.report_sequence }
        elseif ($legacy.payload.report_sequence) { $sequence = [long]$legacy.payload.report_sequence }
        if ($sequence -le 0) { $sequence = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }
        $destination = Join-Path $queuePath ("report-{0:D12}-legacy.json" -f $sequence)
        if (-not (Test-Path -LiteralPath $destination)) { Write-JsonAtomic -Path $destination -Value $legacy }
        Remove-Item -LiteralPath $legacyPendingPath -Force
    }
    catch {
        $destination = Join-Path $quarantinePath ("legacy-{0}.json" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
        Move-Item -LiteralPath $legacyPendingPath -Destination $destination -Force
        Write-RelayHealth -State "quarantined" -Message "An unreadable legacy report was quarantined: $($_.Exception.Message)"
    }
}

function Send-OldestReport {
    $next = Get-ChildItem -LiteralPath $queuePath -Filter "report-*.json" -File -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -First 1
    if ($null -eq $next) {
        Write-RelayHealth -State "waiting" -Message "The relay is ready; no reports are waiting."
        return $true
    }
    $envelope = $null
    try {
        $envelope = Read-JsonFile $next.FullName
        if (-not $envelope.receiver_url -or -not $envelope.sync_token -or ($null -eq $envelope.payload -and -not $envelope.payload_json)) {
            throw "The queued report is missing its receiver, token, or payload."
        }
        $headers = @{ Authorization = "Bearer $($envelope.sync_token)" }
        $body = if ($envelope.payload_json) { [string]$envelope.payload_json } else { $envelope.payload | ConvertTo-Json -Depth 20 -Compress }
        $response = Invoke-RestMethod -Uri $envelope.receiver_url -Method Post -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec 30
        if ($response.status -eq "rejected" -or $response.permanent_rejection -eq $true) {
            $destination = Join-Path $quarantinePath $next.Name
            Move-Item -LiteralPath $next.FullName -Destination $destination -Force
            $result = @{
                ok = $false; permanent_rejection = $true; reason = $response.reason
                message = $response.message; report_sequence = $envelope.report_sequence
                quarantined_as = $next.Name; attempted_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-JsonAtomic -Path $resultPath -Value $result
            Write-RelayHealth -State "needs_attention" -Message ([string]$response.message) -Envelope $envelope
            return $true
        }
        if ($response.ok -ne $true) { throw "The tracker did not acknowledge the report." }
        Remove-Item -LiteralPath $next.FullName -Force
        $result = @{
            ok = $true; status = 200; receiver_ok = $true
            duplicate = [bool]$response.duplicate
            report_sequence = $envelope.report_sequence
            report_checksum = $envelope.report_checksum
            tracker_global_day = $response.tracker_global_day
            sent_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        Write-JsonAtomic -Path $resultPath -Value $result
        Write-RelayHealth -State "connected" -Message "The oldest queued report was accepted." -Envelope $envelope
        return $true
    }
    catch {
        $message = $_.Exception.Message
        if (($null -eq $envelope -or $message -like "*missing its receiver*") -and (Test-Path -LiteralPath $next.FullName)) {
            $destination = Join-Path $quarantinePath $next.Name
            Move-Item -LiteralPath $next.FullName -Destination $destination -Force
            Write-RelayHealth -State "quarantined" -Message "An unreadable or incomplete queue file was quarantined: $message"
            return $true
        }
        if ($message -match '(401|403|Invalid clock token)') {
            Write-RelayHealth -State "needs_attention" -Message "The private link was rejected. Download a fresh config.json from the tracker."
        }
        else {
            Write-RelayHealth -State "offline_queueing" -Message $message -Envelope $envelope
        }
        Write-JsonAtomic -Path $resultPath -Value @{
            ok = $false; error = $message; report_sequence = if ($null -ne $envelope) { $envelope.report_sequence } else { $null }
            attempted_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        return $false
    }
}

try {
    $ownsMutex = $mutex.WaitOne(0, $false)
}
catch [System.Threading.AbandonedMutexException] {
    $ownsMutex = $true
}
if (-not $ownsMutex) { $mutex.Dispose(); exit 0 }

try {
    New-Item -ItemType Directory -Path $queuePath -Force | Out-Null
    New-Item -ItemType Directory -Path $quarantinePath -Force | Out-Null
    if ($SelfTest) {
        $ok = Test-ClockSyncInstall
        if ($ok) { exit 0 } else { exit 1 }
    }
    while ($true) {
        Import-LegacyPendingReport
        $sent = Send-OldestReport
        if ($Once) { break }
        if ($sent) { Start-Sleep -Milliseconds 400 } else { Start-Sleep -Seconds 3 }
    }
}
finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
