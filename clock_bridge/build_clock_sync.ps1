param(
    [string]$CompatibilityArchive = ""
)

$ErrorActionPreference = "Stop"

$bridgeRoot = $PSScriptRoot
$repoRoot = Split-Path -Parent $bridgeRoot
$source = Join-Path $bridgeRoot "mod_source\severaludo_clock_sync\__init__.py"
$compiler = Join-Path (Split-Path -Parent $repoRoot) "SeveralUDO-recovery\tools\python37\python.exe"
$output = Join-Path $bridgeRoot "SeveralUDOClockSync.ts4script"
$staging = Join-Path ([System.IO.Path]::GetTempPath()) "severaludo_clock_sync_204"
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$resolvedStaging = [System.IO.Path]::GetFullPath($staging)

if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw "The Sims Python 3.7 compiler was not found." }
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Clock Sync 2.0.4 source was not found." }
if (-not (Test-Path -LiteralPath $output -PathType Leaf)) { throw "The previous Clock Sync archive was not found." }
if (-not $resolvedStaging.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe Clock Sync staging path." }

$compatibilityInput = $output
$allowLegacyInitializer = $false
if (-not [string]::IsNullOrWhiteSpace($CompatibilityArchive)) {
    $compatibilityInput = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($CompatibilityArchive)
    $allowLegacyInitializer = $true
}
if (-not (Test-Path -LiteralPath $compatibilityInput -PathType Leaf)) {
    throw "The Clock Sync compatibility archive was not found: $compatibilityInput"
}

if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
$module = New-Item -ItemType Directory -Path (Join-Path $staging "severaludo_clock_sync") -Force

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($output)
try {
    $entryName = "severaludo_clock_sync/core.pyc"
    $entry = $archive.Entries | Where-Object { $_.FullName.Replace('\', '/') -eq $entryName } | Select-Object -First 1
    if ($null -eq $entry) { throw "The previous archive is missing $entryName." }
    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $module.FullName "core.pyc"), $true)
} finally {
    $archive.Dispose()
}

# Preserve the actual 2.0.1 compatibility module. Older build scripts renamed
# the previous version's wrapper on every rebuild, eventually making the
# wrapper import itself and crash with a missing `_core` attribute.
$compatArchive = [System.IO.Compression.ZipFile]::OpenRead($compatibilityInput)
try {
    $compatEntryName = "severaludo_clock_sync/compat_201.pyc"
    $compatEntry = $compatArchive.Entries | Where-Object { $_.FullName.Replace('\', '/') -eq $compatEntryName } | Select-Object -First 1
    if ($null -eq $compatEntry -and $allowLegacyInitializer) {
        $legacyEntryName = "severaludo_clock_sync/__init__.pyc"
        $compatEntry = $compatArchive.Entries | Where-Object { $_.FullName.Replace('\', '/') -eq $legacyEntryName } | Select-Object -First 1
    }
    if ($null -eq $compatEntry) {
        throw "The archive is missing compat_201.pyc. Supply an intact legacy 2.0.1 archive with -CompatibilityArchive for one-time recovery."
    }
    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($compatEntry, (Join-Path $module.FullName "compat_201.pyc"), $true)
} finally {
    $compatArchive.Dispose()
}

$compatCompiled = Join-Path $module.FullName "compat_201.pyc"
$validateCompat = "import marshal,sys; f=open(sys.argv[1],'rb'); f.read(16); c=marshal.load(f); assert 'core' in c.co_names, 'compatibility module does not import core'; assert 'compat_201' not in c.co_names, 'compatibility module is a recursive wrapper'"
& $compiler -c $validateCompat $compatCompiled
if ($LASTEXITCODE -ne 0) { throw "Clock Sync compatibility validation failed." }

$compiled = Join-Path $module.FullName "__init__.pyc"
& $compiler -c "import py_compile; py_compile.compile(r'$source', cfile=r'$compiled', doraise=True)"
if ($LASTEXITCODE -ne 0) { throw "Clock Sync 2.0.4 compilation failed." }

$validateWrapper = "import marshal,sys; f=open(sys.argv[1],'rb'); f.read(16); c=marshal.load(f); assert 'compat_201' in c.co_names, 'wrapper does not import compatibility module'"
& $compiler -c $validateWrapper $compiled
if ($LASTEXITCODE -ne 0) { throw "Clock Sync 2.0.4 wrapper validation failed." }

$temporaryZip = [System.IO.Path]::ChangeExtension($output, ".zip")
if (Test-Path -LiteralPath $temporaryZip) { Remove-Item -LiteralPath $temporaryZip -Force }
Compress-Archive -Path $module.FullName -DestinationPath $temporaryZip -CompressionLevel Optimal
Move-Item -LiteralPath $temporaryZip -Destination $output -Force
Write-Output $output
