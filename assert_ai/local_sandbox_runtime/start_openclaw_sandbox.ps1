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
    [string]$SnapshotRoot = "",
    [string]$RuntimeCommandFile = "",
    [string]$IdentityStagingFile = "",
    [int]$EndpointPort = 0,
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

$workspace = Join-Path $env:TEMP "openclaw-sandbox-workspace-$SandboxName"

function Resolve-HostPath {
    param([string]$PathValue)
    if (-not $PathValue) { return "" }
    if ((Test-Path -LiteralPath $PathValue) -or (-not $PathValue.StartsWith("/"))) {
        return $PathValue
    }
    return (wsl.exe wslpath -w $PathValue).Trim()
}

function Copy-DirectoryContents {
    param(
        [string]$Source,
        [string]$Destination
    )
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}

if ($RuntimeArchive) {
    $runtimeArchivePath = Resolve-HostPath $RuntimeArchive
    if (-not (Test-Path -LiteralPath $runtimeArchivePath)) {
        throw "Runtime archive not found: $RuntimeArchive"
    }
    New-Item -ItemType Directory -Force -Path $workspace | Out-Null
    Copy-Item -LiteralPath $runtimeArchivePath -Destination (Join-Path $workspace "openclaw-runtime.tar.gz") -Force
}

if ($SnapshotRoot -or $RuntimeCommandFile -or $IdentityStagingFile) {
    New-Item -ItemType Directory -Force -Path $workspace | Out-Null
}
if ($SnapshotRoot) {
    $snapshotRootPath = Resolve-HostPath $SnapshotRoot
    if (-not (Test-Path -LiteralPath $snapshotRootPath)) {
        throw "Snapshot root not found: $SnapshotRoot"
    }
    Copy-DirectoryContents -Source $snapshotRootPath -Destination (Join-Path $workspace "snapshot")
}
if ($RuntimeCommandFile) {
    $runtimeCommandPath = Resolve-HostPath $RuntimeCommandFile
    if (-not (Test-Path -LiteralPath $runtimeCommandPath)) {
        throw "Runtime command file not found: $RuntimeCommandFile"
    }
    Copy-Item -LiteralPath $runtimeCommandPath -Destination (Join-Path $workspace "runtime-command.json") -Force
}
if ($IdentityStagingFile) {
    $identityStagingPath = Resolve-HostPath $IdentityStagingFile
    if (-not (Test-Path -LiteralPath $identityStagingPath)) {
        throw "Identity staging file not found: $IdentityStagingFile"
    }
    Copy-Item -LiteralPath $identityStagingPath -Destination (Join-Path $workspace "identity-staging.json") -Force
}

$invokeParams = @{
    Models = $Models
    AuthProxyPort = $AuthProxyPort
    SandboxName = $SandboxName
}
if ($SkipBuild) { $invokeParams["SkipBuild"] = $true }
if ($SnapshotRoot) { $invokeParams["SnapshotRoot"] = "snapshot" }
if ($RuntimeCommandFile) { $invokeParams["RuntimeCommandFile"] = "runtime-command.json" }
if ($IdentityStagingFile) { $invokeParams["IdentityStagingFile"] = "identity-staging.json" }
if ($EndpointPort -gt 0) { $invokeParams["EndpointPort"] = $EndpointPort }

& $launcher @invokeParams
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
