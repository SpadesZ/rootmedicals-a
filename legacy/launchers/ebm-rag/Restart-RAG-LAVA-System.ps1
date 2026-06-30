# File Path: rootmedicals-a/legacy/launchers/ebm-rag/Restart-RAG-LAVA-System.ps1
# Timestamp: 2026-06-17 15:05 +08:00
# Version: v0.2
# Description:
#   Restarts the rootmedicals-a EBM-RAG/LAVA Docker stack and verifies that the
#   live /api/lava/tasks endpoint exposes the current task registry.
# Change Notes:
#   - v0.1: Added Docker compose restart, health polling, task-count assertion,
#     and visible task id output for the Phase 3/4 LAVA task mismatch.
#   - v0.2: Move to legacy launchers and resolve the active ebm-rag compose
#     folder from the rootmedicals-a project root.
# Safety Notes:
#   - Operates only on the compose project in this ebm-rag folder. It does not
#     delete volumes, API keys, LAVA connections, RAG data, or external folders.
# Verification Notes:
#   - Validate by running this script and checking that clinical_soap_parse and
#     llmaaj_adjudicate appear in the output.
# ----------------------------------------------------------------------------------------------------

param(
    [int]$Port = 33301,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $PSCommandPath
$RootDir = (Resolve-Path -LiteralPath (Join-Path $ScriptDir "..\..\..")).Path
$RagDir = Join-Path $RootDir "ebm-rag"
$ComposeFile = Join-Path $RagDir "docker-compose-rag.yml"
$TasksUrl = "http://127.0.0.1:$Port/api/lava/tasks"
$HealthUrl = "http://127.0.0.1:$Port/api/v1/rag/health"

function Write-Step {
    param([string]$Message)
    Write-Host "[RAG-LAVA] $Message"
}

function Invoke-DockerCompose {
    param([string[]]$ComposeArgs)
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $docker) {
        throw "Docker CLI was not found. Start Docker Desktop and ensure docker is in PATH."
    }
    & docker compose -f $ComposeFile @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed: $($ComposeArgs -join ' ')"
    }
}

function Test-JsonEndpoint {
    param([string]$Url)
    try {
        Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "Compose file not found: $ComposeFile"
}

Write-Step "Folder: $RagDir"
Write-Step "Compose: $ComposeFile"

$upArgs = @("up", "-d", "--force-recreate")
if (-not $NoBuild) {
    $upArgs += "--build"
}
Invoke-DockerCompose -ComposeArgs $upArgs

$ready = $false
for ($i = 1; $i -le 40; $i++) {
    Start-Sleep -Milliseconds 750
    if (Test-JsonEndpoint -Url $HealthUrl) {
        $ready = $true
        break
    }
}
if (-not $ready) {
    throw "RAG health endpoint did not become ready: $HealthUrl"
}

$tasks = Invoke-RestMethod -Uri $TasksUrl -Method Get -TimeoutSec 5
if (-not ($tasks -is [System.Array])) {
    throw "LAVA tasks endpoint did not return an array: $TasksUrl"
}
$taskIds = @($tasks | ForEach-Object { [string]$_.task_id })
Write-Step "Live tasks ($($taskIds.Count)): $($taskIds -join ', ')"
if ($taskIds.Count -lt 8 -or -not ($taskIds -contains "clinical_soap_parse") -or -not ($taskIds -contains "llmaaj_adjudicate")) {
    throw "Live LAVA task registry is stale. Expected clinical_soap_parse and llmaaj_adjudicate."
}
Write-Step "LAVA task registry is current."
