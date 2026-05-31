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

function Write-Step { param($msg) Write-Host "`n$(Get-Date -Format 'HH:mm:ss') ==> $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "$(Get-Date -Format 'HH:mm:ss')     [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "$(Get-Date -Format 'HH:mm:ss')     [!!] $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "$(Get-Date -Format 'HH:mm:ss')     [XX] $msg" -ForegroundColor Red }

# --- 1. Ollama ---
# Port 11434 falls in a Windows/Hyper-V excluded range on this machine.
# Use 11600 instead. Set OLLAMA_HOST so both `ollama serve` and the
# ollama Python client bind/connect to the same port.
$env:OLLAMA_HOST = '127.0.0.1:11600'
$ollamaUrl = 'http://127.0.0.1:11600'

Write-Step "Checking Ollama (port 11600)"
$ollamaOk = $false
try {
    Invoke-RestMethod -Uri "$ollamaUrl/api/version" -TimeoutSec 3 | Out-Null
    $ollamaOk = $true
    Write-OK "Ollama is running"
} catch {
    Write-Host "$(Get-Date -Format 'HH:mm:ss')     [..] Ollama not detected — launching ollama serve..." -ForegroundColor Gray
    try {
        Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden -ErrorAction Stop
        # Wait up to 10 s for it to come up
        $waited = 0
        while ($waited -lt 10) {
            Start-Sleep -Seconds 1
            $waited++
            try {
                Invoke-RestMethod -Uri "$ollamaUrl/api/version" -TimeoutSec 1 | Out-Null
                $ollamaOk = $true
                Write-OK "Ollama started (after $waited s)"
                break
            } catch {}
        }
        if (-not $ollamaOk) {
            Write-Warn "Ollama did not respond after 10 s. LLM features may fail."
        }
    } catch {
        Write-Warn "Could not launch ollama serve: $_"
        Write-Host "    Start Ollama manually or check that 'ollama' is on your PATH." -ForegroundColor Gray
    }
}

if ($ollamaOk) {
    $requiredModels = @('nomic-embed-text', 'minicpm-v', 'qwen2.5:14b')
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

# --- 3. Start DocVault (auto-restart on crash) ---
Write-Host ""
Write-Host "  Starting DocVault at http://localhost:8050  (Ctrl+C to stop)" -ForegroundColor Cyan
Write-Host ""

$maxRestarts  = 10
$restartCount = 0
$delaySecs    = 5

while ($true) {
    & $venvPython (Join-Path $scriptDir 'run.py')
    $exitCode = $LASTEXITCODE

    # Exit code 0 = clean shutdown (Ctrl+C / user stop). Don't restart.
    if ($exitCode -eq 0) {
        Write-Host "$(Get-Date -Format 'HH:mm:ss') DocVault stopped cleanly." -ForegroundColor Green
        break
    }

    $restartCount++
    if ($restartCount -gt $maxRestarts) {
        Write-Fail "DocVault has crashed $maxRestarts time(s) in a row. Giving up. Check logs."
        break
    }

    $msg = "DocVault exited (code $exitCode, crash $restartCount of $maxRestarts). Restarting in $delaySecs s..."
    Write-Warn $msg
    Start-Sleep -Seconds $delaySecs
    $delaySecs = [Math]::Min($delaySecs * 2, 60)
}
