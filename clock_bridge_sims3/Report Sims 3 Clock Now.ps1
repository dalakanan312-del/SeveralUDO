param(
    [int]$GameDay = 0,
    [int]$Hour = -1,
    [int]$Minute = -1,
    [string]$SaveIdentity = ""
)

$ErrorActionPreference = "Stop"
$relayRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $relayRoot "config.json"
$queuePath = Join-Path $relayRoot "report_queue"
$statePath = Join-Path $relayRoot "sims3_reporter_state.json"

function Read-JsonFile {
    param([string]$Path)
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Write-JsonAtomic {
    param([string]$Path, [object]$Value)
    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 20 -Compress | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function Read-ClockNumber {
    param([string]$Label, [int]$Minimum, [int]$Maximum, [int]$Current)
    while ($true) {
        $value = if ($Current -ge $Minimum) { $Current } else { [int](Read-Host $Label) }
        if ($value -ge $Minimum -and $value -le $Maximum) { return $value }
        Write-Host "Enter a number from $Minimum to $Maximum."
        $Current = -1
    }
}

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "config.json is missing. Download a private Sims 3 Clock Sync kit from the tracker."
}
$config = Read-JsonFile $configPath
if (-not $config.receiver_url -or -not $config.sync_token -or $config.enabled -eq $false) {
    throw "config.json is incomplete or this link is disabled. Download a fresh private kit from the tracker."
}
if ([string]$config.game_edition -and [string]$config.game_edition -ne "sims3") {
    throw "This config belongs to a different game edition. Download the Sims 3 private kit from the tracker."
}
New-Item -ItemType Directory -Path $queuePath -Force | Out-Null
$state = if (Test-Path -LiteralPath $statePath -PathType Leaf) { Read-JsonFile $statePath } else { [pscustomobject]@{} }
$GameDay = Read-ClockNumber -Label "Current Sims 3 game day" -Minimum 1 -Maximum 1000000 -Current $GameDay
$Hour = Read-ClockNumber -Label "Current in-game hour (0-23)" -Minimum 0 -Maximum 23 -Current $Hour
$Minute = Read-ClockNumber -Label "Current in-game minute (0-59)" -Minimum 0 -Maximum 59 -Current $Minute
if (-not $SaveIdentity) { $SaveIdentity = [string]$config.sims3_save_identity }
if (-not $SaveIdentity) { $SaveIdentity = [string]$state.save_identity }
if (-not $SaveIdentity) { $SaveIdentity = Read-Host "Name this Sims 3 save (used to keep it paired with this tracker save)" }
if (-not $SaveIdentity) { throw "A Sims 3 save name is required for safe pairing." }
$sequence = [long]($state.last_sequence) + 1
$report = [ordered]@{
    protocol_version = 1
    clock_sync_version = "Sims3-0.1.0-manual-bridge"
    game_edition = "sims3"
    report_sequence = $sequence
    report_id = [guid]::NewGuid().ToString("N")
    report_kind = "clock"
    save_identity = $SaveIdentity
    save_slot_name = $SaveIdentity
    game_day = $GameDay
    hour = $Hour
    minute = $Minute
    second = 0
    household_members = @()
    population_complete = $false
    generated_at = [DateTimeOffset]::UtcNow.ToString("o")
}
$envelope = [ordered]@{
    receiver_url = [string]$config.receiver_url
    sync_token = [string]$config.sync_token
    report_sequence = $sequence
    payload = $report
}
$destination = Join-Path $queuePath ("report-{0:D12}-sims3.json" -f $sequence)
Write-JsonAtomic -Path $destination -Value $envelope
Write-JsonAtomic -Path $statePath -Value ([ordered]@{ last_sequence = $sequence; save_identity = $SaveIdentity; last_reported_at = [DateTimeOffset]::UtcNow.ToString("o") })
Write-Host "Queued Sims 3 day $GameDay at $($Hour.ToString('00')):$($Minute.ToString('00')). The relay will deliver it shortly."
