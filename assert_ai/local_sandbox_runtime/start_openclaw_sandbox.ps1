<#
.SYNOPSIS
  Product wrapper around microsoft/rampart-examples/openclaw/scripts/openclaw-sandbox.ps1.

.DESCRIPTION
  Stages a copied OpenClaw runtime archive into Docker Sandbox's Windows-local
  temp workspace, then delegates sandbox creation/configuration to the existing
  RAMPART OpenClaw launcher. This keeps ASSERT's local-agent sandbox path on the
  proven Docker/OpenClaw runner while presenting a clean product command.
#>
param(
    [string]$RampartOpenClawRoot,
    [string]$SandboxName = "oc-local-agent",
    [Parameter(Mandatory=$true)]
    [string]$Models,
    [int]$AuthProxyPort = 12435,
    [string]$RuntimeArchive = "",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$rampartRootPath = $RampartOpenClawRoot
if (-not (Test-Path -LiteralPath $rampartRootPath) -and $RampartOpenClawRoot.StartsWith("/")) {
    $rampartRootPath = (wsl.exe wslpath -w $RampartOpenClawRoot).Trim()
}
$launcher = Join-Path $rampartRootPath "scripts\openclaw-sandbox.ps1"
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "OpenClaw sandbox launcher not found: $launcher"
}

if ($RuntimeArchive) {
    $runtimeArchivePath = $RuntimeArchive
    if (-not (Test-Path -LiteralPath $runtimeArchivePath) -and $RuntimeArchive.StartsWith("/")) {
        $runtimeArchivePath = (wsl.exe wslpath -w $RuntimeArchive).Trim()
    }
    if (-not (Test-Path -LiteralPath $runtimeArchivePath)) {
        throw "Runtime archive not found: $RuntimeArchive"
    }
    $workspace = Join-Path $env:TEMP "openclaw-sandbox-workspace-$SandboxName"
    New-Item -ItemType Directory -Force -Path $workspace | Out-Null
    Copy-Item -LiteralPath $runtimeArchivePath -Destination (Join-Path $workspace "openclaw-runtime.tar.gz") -Force
}

$invokeParams = @{
    Models = $Models
    AuthProxyPort = $AuthProxyPort
    SandboxName = $SandboxName
}
if ($SkipBuild) { $invokeParams["SkipBuild"] = $true }

& $launcher @invokeParams
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
