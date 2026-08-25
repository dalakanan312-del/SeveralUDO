$ErrorActionPreference = "Stop"

$bridgeRoot = $PSScriptRoot
$repoRoot = Split-Path -Parent $bridgeRoot
$source = Join-Path $bridgeRoot "mod_source\severaludo_clock_sync\__init__.py"
$compiler = Join-Path (Split-Path -Parent $repoRoot) "SeveralUDO-recovery\tools\python37\python.exe"
$output = Join-Path $bridgeRoot "SeveralUDOClockSync.ts4script"
$staging = Join-Path ([System.IO.Path]::GetTempPath()) "severaludo_clock_sync_203"
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$resolvedStaging = [System.IO.Path]::GetFullPath($staging)

if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw "The Sims Python 3.7 compiler was not found." }
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Clock Sync 2.0.3 source was not found." }
if (-not (Test-Path -LiteralPath $output -PathType Leaf)) { throw "The previous Clock Sync archive was not found." }
if (-not $resolvedStaging.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe Clock Sync staging path." }

if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
$module = New-Item -ItemType Directory -Path (Join-Path $staging "severaludo_clock_sync") -Force

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($output)
try {
    foreach ($entryName in @("severaludo_clock_sync/core.pyc", "severaludo_clock_sync/__init__.pyc")) {
        $entry = $archive.Entries | Where-Object { $_.FullName.Replace('\', '/') -eq $entryName } | Select-Object -First 1
        if ($null -eq $entry) { throw "The previous archive is missing $entryName." }
        $destinationName = if ($entryName.EndsWith("/__init__.pyc")) { "compat_201.pyc" } else { "core.pyc" }
        [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $module.FullName $destinationName), $true)
    }
} finally {
    $archive.Dispose()
}

$compiled = Join-Path $module.FullName "__init__.pyc"
& $compiler -c "import py_compile; py_compile.compile(r'$source', cfile=r'$compiled', doraise=True)"
if ($LASTEXITCODE -ne 0) { throw "Clock Sync 2.0.3 compilation failed." }

$temporaryZip = [System.IO.Path]::ChangeExtension($output, ".zip")
if (Test-Path -LiteralPath $temporaryZip) { Remove-Item -LiteralPath $temporaryZip -Force }
Compress-Archive -Path $module.FullName -DestinationPath $temporaryZip -CompressionLevel Optimal
Move-Item -LiteralPath $temporaryZip -Destination $output -Force
Write-Output $output
