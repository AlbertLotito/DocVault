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
# Windows/Hyper-V (and WSL2's NAT) reserve dynamic TCP port-exclusion ranges
# that are re-randomised on every reboot. A port that's free today can start
# failing with "bind: An attempt was made to access a socket in a way
# forbidden by its access permissions" after the next reboot with zero
# warning - that's what took down port 11434 originally, and later 11600.
# Rather than trust a hardcoded port, verify it's still clear on every start
# and transparently move to a free one (updating config.ini + OLLAMA_HOST)
# if it isn't.

$configPath = Join-Path $scriptDir 'config.ini'

function Get-ExcludedTcpRanges {
    $ranges = @()
    netsh interface ipv4 show excludedportrange protocol=tcp 2>$null | ForEach-Object {
        if ($_ -match '^\s*(\d+)\s+(\d+)\s*\*?\s*$') {
            $ranges += [PSCustomObject]@{ Start = [int]$Matches[1]; End = [int]$Matches[2] }
        }
    }
    return $ranges
}

function Test-PortFree {
    param([int]$Port, [array]$ExcludedRanges)
    foreach ($r in $ExcludedRanges) {
        if ($Port -ge $r.Start -and $Port -le $r.End) { return $false }
    }
    return -not (Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Find-FreeOllamaPort {
    param([int]$Preferred)
    $excluded = Get-ExcludedTcpRanges
    if (Test-PortFree -Port $Preferred -ExcludedRanges $excluded) { return $Preferred }
    Write-Warn "Port $Preferred is now inside a Windows/Hyper-V excluded range (or in use) - picking a new one"
    foreach ($candidate in 11600..11699) {
        if (Test-PortFree -Port $candidate -ExcludedRanges $excluded) { return $candidate }
    }
    return $null
}

function Update-ConfigOllamaHost {
    param([string]$NewUrl)
    $lines = Get-Content $configPath
    $inOllamaSection = $false
    $out = foreach ($line in $lines) {
        if ($line -match '^\[(.+)\]\s*$') { $inOllamaSection = ($Matches[1] -eq 'ollama') }
        if ($inOllamaSection -and $line -match '^host\s*=') {
            "host = $NewUrl"
        } else {
            $line
        }
    }
    # Set-Content -Encoding UTF8 writes a BOM, which Python's configparser
    # (core/settings.py) chokes on with "File contains no section headers".
    # -Encoding utf8NoBOM only exists on PowerShell 6+; this .NET call works
    # on both 5.1 and 7+ (script requires 5.1).
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllLines($configPath, $out, $utf8NoBom)
}

# Preferred port: whatever config.ini currently has for [ollama] host,
# falling back to 11600 if that can't be parsed.
$preferredPort = 11600
if ((Get-Content $configPath -Raw) -match '(?ms)^\[ollama\][^\[]*?^host\s*=\s*http://[^:]+:(\d+)') {
    $preferredPort = [int]$Matches[1]
}

$ollamaPort = Find-FreeOllamaPort -Preferred $preferredPort
if (-not $ollamaPort) {
    Write-Fail "Could not find any free TCP port for Ollama in range 11600-11699. LLM features will fail."
    $ollamaPort = $preferredPort
} elseif ($ollamaPort -ne $preferredPort) {
    Update-ConfigOllamaHost -NewUrl "http://localhost:$ollamaPort"
    Write-OK "config.ini [ollama] host updated to port $ollamaPort (was $preferredPort)"
}

$ollamaHostEnv = "127.0.0.1:$ollamaPort"
if ([System.Environment]::GetEnvironmentVariable('OLLAMA_HOST', 'User') -ne $ollamaHostEnv) {
    [System.Environment]::SetEnvironmentVariable('OLLAMA_HOST', $ollamaHostEnv, 'User')
    Write-Host "$(Get-Date -Format 'HH:mm:ss')     [OK] OLLAMA_HOST set persistently to $ollamaHostEnv" -ForegroundColor Green
}
$env:OLLAMA_HOST = $ollamaHostEnv
$ollamaUrl = "http://127.0.0.1:$ollamaPort"

function Start-OllamaOnPort {
    param([string]$Url)
    # The Windows Ollama app enforces a single-instance lock that is
    # independent of which port it ends up bound to. If a previous run
    # left a stale process (or a stale lock with no visible process) behind,
    # a fresh `ollama serve` silently no-ops ("existing instance found,
    # exiting" in %LOCALAPPDATA%\Ollama\app.log) instead of binding the
    # requested port. Clearing any native ollama.exe process first avoids that.
    Get-Process -Name 'ollama*' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden -ErrorAction Stop
    $waited = 0
    while ($waited -lt 10) {
        Start-Sleep -Seconds 1
        $waited++
        try {
            Invoke-RestMethod -Uri "$Url/api/version" -TimeoutSec 1 | Out-Null
            return $waited
        } catch {}
    }
    return $null
}

Write-Step "Checking Ollama (port $ollamaPort)"
$ollamaOk = $false
try {
    Invoke-RestMethod -Uri "$ollamaUrl/api/version" -TimeoutSec 3 | Out-Null
    $ollamaOk = $true
    Write-OK "Ollama is running"
} catch {
    Write-Host "$(Get-Date -Format 'HH:mm:ss')     [..] Ollama not detected on $ollamaPort - launching ollama serve..." -ForegroundColor Gray
    try {
        $waited = Start-OllamaOnPort -Url $ollamaUrl
        if ($waited) {
            $ollamaOk = $true
            Write-OK "Ollama started (after $waited s)"
        } else {
            # One retry: the single-instance lock can still be settling
            # from the process we just killed.
            Write-Host "$(Get-Date -Format 'HH:mm:ss')     [..] Retrying once (stale instance lock)..." -ForegroundColor Gray
            $waited = Start-OllamaOnPort -Url $ollamaUrl
            if ($waited) {
                $ollamaOk = $true
                Write-OK "Ollama started (after $waited s, retry)"
            }
        }
        if (-not $ollamaOk) {
            Write-Warn "Ollama did not respond on $ollamaPort after retrying. LLM features may fail."
            Write-Host "    Run 'ollama serve' manually in another terminal to see the real bind error." -ForegroundColor Gray
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
                Write-Host "$(Get-Date -Format 'HH:mm:ss')     [..] Pulling missing model: $m ..." -ForegroundColor Gray
                & ollama pull $m
                if ($LASTEXITCODE -eq 0) {
                    Write-OK "Model pulled: $m"
                } else {
                    Write-Warn "Failed to pull $m - run manually: ollama pull $m"
                }
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
