[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$pluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifestPath = Join-Path $pluginRoot ".codex-plugin\plugin.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$pluginName = [string]$manifest.name
$version = [string]$manifest.version

if ($pluginName -notmatch '^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$') {
    throw "Invalid plugin name in manifest: $pluginName"
}
if ([string]::IsNullOrWhiteSpace($version)) {
    throw "Plugin version is missing"
}

$releaseRoot = Join-Path $pluginRoot "release"
$stagingRoot = Join-Path ([IO.Path]::GetTempPath()) ("project-workflow-package-" + [guid]::NewGuid().ToString("N"))
$stagedPlugin = Join-Path $stagingRoot $pluginName
$archivePath = Join-Path $releaseRoot "$pluginName-$version.zip"
$checksumPath = "$archivePath.sha256"

try {
    New-Item -ItemType Directory -Force -Path $stagedPlugin | Out-Null
    Get-ChildItem -Force -LiteralPath $pluginRoot |
        Where-Object { $_.Name -notin @(".git", ".workflow", "release", "__pycache__") } |
        ForEach-Object { Copy-Item -Recurse -Force -LiteralPath $_.FullName -Destination $stagedPlugin }

    Get-ChildItem -LiteralPath $stagedPlugin -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $stagedPlugin -Recurse -File -Filter "*.pyc" |
        Remove-Item -Force

    New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
    Compress-Archive -LiteralPath $stagedPlugin -DestinationPath $archivePath -Force
    $hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($archivePath))" | Set-Content -LiteralPath $checksumPath -Encoding ASCII

    Write-Host "Created $archivePath"
    Write-Host "Created $checksumPath"
} finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
