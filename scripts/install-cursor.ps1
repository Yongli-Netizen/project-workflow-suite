[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$pluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$targetRoot = Join-Path $env:USERPROFILE ".cursor\plugins\local\project-workflow-suite"
$sourceFull = [IO.Path]::GetFullPath($pluginRoot).TrimEnd('\')
$targetFull = [IO.Path]::GetFullPath($targetRoot).TrimEnd('\')

$codebaseMemory = Get-Command "codebase-memory-mcp" -ErrorAction SilentlyContinue
$fallback = Join-Path $env:LOCALAPPDATA "Programs\codebase-memory-mcp\codebase-memory-mcp.exe"
if (-not $codebaseMemory -and -not (Test-Path -LiteralPath $fallback -PathType Leaf)) {
    throw "codebase-memory-mcp is required but was not found on PATH. Install it before enabling this plugin in Cursor."
}

if ($sourceFull -eq $targetFull) {
    Write-Host "Project Workflow Suite is already in Cursor's local plugin directory."
} else {
    New-Item -ItemType Directory -Force -Path $targetRoot | Out-Null
    Get-ChildItem -Force -LiteralPath $pluginRoot |
        Where-Object { $_.Name -notin @(".git", ".workflow", "release", "__pycache__") } |
        ForEach-Object { Copy-Item -Recurse -Force -LiteralPath $_.FullName -Destination $targetRoot }
    Get-ChildItem -LiteralPath $targetRoot -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $targetRoot -Recurse -File -Filter "*.pyc" |
        Remove-Item -Force
    Write-Host "Installed Project Workflow Suite for Cursor at $targetRoot"
}

Write-Host "Restart Cursor or run 'Developer: Reload Window', then enable the plugin from Customize."
