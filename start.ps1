#Requires -Version 5.1
<#
.SYNOPSIS
    Start DocVault. Run this every time you want to use the system.
    Warns if Ollama is missing, then starts the app.
.EXAMPLE
    .\start.ps1
#>

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "    [!!] $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "    [XX] $msg" -ForegroundColor Red }

# --- 1. Ollama (warn only, do not block startup) ---
Write-Step "Checking Ollama"
$ollamaOk = $false
try {
    Invoke-RestMethod -Uri 'http://localhost:11434/api/version' -TimeoutSec 3 | Out-Null
    $ollamaOk = $true
    Write-OK "Ollama is running"
} catch {
    Write-Warn "Ollama is not running. Embeddings and LLM features will fail until it starts."
    Write-Host "    Start Ollama from the system tray or run: ollama serve" -ForegroundColor Gray
}

if ($ollamaOk) {
    $requiredModels = @('nomic-embed-text-v2', 'minicpm-v', 'qwen2.5:14b')
    try {
        $ollamaList = (& ollama list 2>&1) | Out-String
        foreach ($m in $requiredModels) {
            if ($ollamaList -like "*$m*") {
                Write-OK "Model: $m"
            } else {
                Write-Warn "Model not pulled: $m  -- run: ollama pull $m"
            }
        }
    } catch {
        Write-Warn "Could not check Ollama model list"
    }
}

# --- 2. Verify venv ---
Write-Step "Checking Python environment"
$venvPython = Join-Path $scriptDir 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Fail "venv not found. Run setup.ps1 first."
    exit 1
}
Write-OK "venv found"

# --- 3. Start DocVault ---
Write-Host ""
Write-Host "  Starting DocVault at http://localhost:8050  (Ctrl+C to stop)" -ForegroundColor Cyan
Write-Host ""

& $venvPython (Join-Path $scriptDir 'run.py')
