[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ForwardedArgs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$command = Get-Command "codebase-memory-mcp" -ErrorAction SilentlyContinue
if ($command) {
    $executable = $command.Source
} else {
    $executable = Join-Path $env:LOCALAPPDATA "Programs\codebase-memory-mcp\codebase-memory-mcp.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "codebase-memory-mcp was not found on PATH or in its default Windows installation directory."
    }
}

& $executable @ForwardedArgs
exit $LASTEXITCODE
