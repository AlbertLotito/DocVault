#Requires -Version 5.1
<#
.SYNOPSIS
    Start DocVault. Run this every time you want to use the system.
    Ensures Qdrant is running, warns if Ollama is missing models, then starts the app.
.EXAMPLE
    .\start.ps1
#>

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "    [!!] $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "    [XX] $msg" -ForegroundColor Red }

# --- 1. Docker daemon ---
Write-Step "Checking Docker"
try {
    docker version 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker version failed" }
    Write-OK "Docker daemon is running"
} catch {
    Write-Fail "Docker is not running."
    Write-Host "    Start Docker Desktop from the Start Menu and wait for the whale icon in the system tray, then re-run this script." -ForegroundColor Yellow
    exit 1
}

# --- 2. Qdrant container ---
Write-Step "Starting Qdrant"
$qdrantContainer = 'docvault-qdrant-1'

$containerExists = $false
$containerRunning = $false
try {
    $status = docker inspect $qdrantContainer --format '{{.State.Status}}' 2>&1
    if ($LASTEXITCODE -eq 0) {
        $containerExists = $true
        $containerRunning = ($status.Trim() -eq 'running')
    }
} catch {}

if (-not $containerExists) {
    Write-Warn "Container '$qdrantContainer' not found. Creating via docker compose..."
    Push-Location $scriptDir
    docker compose up -d qdrant 2>&1
    Pop-Location
} elseif (-not $containerRunning) {
    docker start $qdrantContainer 2>&1 | Out-Null
    Write-OK "Container started"
} else {
    Write-OK "Container already running"
}

# Wait for Qdrant HTTP health (up to 30s)
Write-Host "    Waiting for Qdrant to accept connections..." -ForegroundColor Gray
$qdrantReady = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-RestMethod -Uri 'http://localhost:6333/health' -TimeoutSec 2
        if ($r.status -eq 'ok') { $qdrantReady = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $qdrantReady) {
    Write-Fail "Qdrant did not become healthy after 30s."
    Write-Host "    Check logs with: docker logs $qdrantContainer" -ForegroundColor Yellow
    exit 1
}
Write-OK "Qdrant healthy at http://localhost:6333"

# --- 3. Ollama (warn only, do not block startup) ---
Write-Step "Checking Ollama"
$ollamaOk = $false
try {
    Invoke-RestMethod -Uri 'http://localhost:11434/api/version' -TimeoutSec 3 | Out-Null
    $ollamaOk = $true
    Write-OK "Ollama is running"
} catch {
    Write-Warn "Ollama is not running. Embeddings and LLM features will fail until it starts."
    Write-Host "    Start Ollama: ollama serve" -ForegroundColor Gray
}

if ($ollamaOk) {
    $requiredModels = @('nomic-embed-text', 'minicpm-v', 'deepseek-r1:14b')
    try {
        $tags = (Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -TimeoutSec 5).models.name
        foreach ($m in $requiredModels) {
            $found = $tags | Where-Object { $_ -like "${m}*" }
            if ($found) {
                Write-OK "Model: $m"
            } else {
                Write-Warn "Model not pulled: $m  -- run: ollama pull $m"
            }
        }
    } catch {
        Write-Warn "Could not check Ollama model list"
    }
}

# --- 4. Verify venv ---
Write-Step "Checking Python environment"
$venvPython = Join-Path $scriptDir 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Fail "venv not found. Run setup.ps1 first."
    exit 1
}
Write-OK "venv found"

# --- 5. Start DocVault ---
Write-Host ""
Write-Host "  Starting DocVault at http://localhost:8000  (Ctrl+C to stop)" -ForegroundColor Cyan
Write-Host ""

& $venvPython (Join-Path $scriptDir 'run.py')
