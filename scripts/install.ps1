[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$pluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pluginName = "project-workflow-suite"
$marketplaceName = "project-workflow-suite"
$marketplaceRoot = Join-Path $env:USERPROFILE ".codex\marketplaces\$marketplaceName"
$targetRoot = Join-Path $marketplaceRoot "plugins\$pluginName"
$marketplacePath = Join-Path $marketplaceRoot ".agents\plugins\marketplace.json"

$codex = Get-Command "codex" -ErrorAction Stop
$cbm = Get-Command "codebase-memory-mcp" -ErrorAction SilentlyContinue
if (-not $cbm) {
    throw "codebase-memory-mcp is required. Install it from https://github.com/DeusData/codebase-memory-mcp, then run this installer again."
}

New-Item -ItemType Directory -Force -Path $targetRoot | Out-Null
Get-ChildItem -Force -LiteralPath $pluginRoot |
    Where-Object { $_.Name -notin @(".git", "__pycache__") } |
    ForEach-Object { Copy-Item -Recurse -Force -LiteralPath $_.FullName -Destination $targetRoot }
Get-ChildItem -LiteralPath $targetRoot -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $targetRoot -Recurse -File -Filter "*.pyc" |
    Remove-Item -Force

$marketplace = [ordered]@{
    name = $marketplaceName
    interface = [ordered]@{ displayName = "Project Workflow Suite" }
    plugins = @([ordered]@{
        name = $pluginName
        source = [ordered]@{ source = "local"; path = "./plugins/$pluginName" }
        policy = [ordered]@{ installation = "AVAILABLE"; authentication = "ON_INSTALL" }
        category = "Productivity"
    })
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $marketplacePath) | Out-Null
$marketplace | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $marketplacePath -Encoding UTF8

& $codex.Source plugin marketplace add $marketplaceRoot
if ($LASTEXITCODE -ne 0) { throw "Codex marketplace registration failed with exit code $LASTEXITCODE" }
& $codex.Source plugin add "$pluginName@$marketplaceName"
if ($LASTEXITCODE -ne 0) { throw "Codex plugin installation failed with exit code $LASTEXITCODE" }

& $cbm.Source --version
if ($LASTEXITCODE -ne 0) { throw "codebase-memory-mcp version check failed" }
Write-Host "Installation complete. Restart Codex and open a new task."
