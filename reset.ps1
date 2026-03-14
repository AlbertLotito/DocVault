#Requires -Version 5.1
<#
.SYNOPSIS
    Reset DocVault to a clean slate.

    DESTROYS:
      - docvault.db   (all tasks, FTS index, extracted text, extracted images)
      - Qdrant vectors (the docvault collection — all embeddings)
      - logs.db       (optional — asked interactively)

    PRESERVES:
      - settings.db   (all config, API keys, vault settings — untouched)
      - Source files  (DocVault never owns your documents)
      - art_index     (Qdrant art collection — untouched)
      - qdrant_storage layout (Qdrant is restarted clean)

    When to use:
      - Changing extraction settings and need a clean re-run from scratch
      - Database corruption
      - Starting fresh after adding a new vault or major config change

.EXAMPLE
    .\reset.ps1
#>

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step  { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "    [!!] $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "    [XX] $msg" -ForegroundColor Red }

# ─────────────────────────────────────────────────────────────────────────────
# Warning and confirmation
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════════╗" -ForegroundColor Red
Write-Host "  ║              DOCVAULT CLEAN SLATE RESET                  ║" -ForegroundColor Red
Write-Host "  ╠══════════════════════════════════════════════════════════╣" -ForegroundColor Red
Write-Host "  ║  This will permanently delete:                           ║" -ForegroundColor Red
Write-Host "  ║    • docvault.db  (all tasks and extracted text)         ║" -ForegroundColor Red
Write-Host "  ║    • Qdrant docvault collection (all embeddings)         ║" -ForegroundColor Red
Write-Host "  ║                                                          ║" -ForegroundColor Red
Write-Host "  ║  settings.db is NOT touched. Your config, API keys,      ║" -ForegroundColor Red
Write-Host "  ║  and vault settings are fully preserved.                 ║" -ForegroundColor Red
Write-Host "  ║                                                          ║" -ForegroundColor Red
Write-Host "  ║  Source files on disk are NEVER touched.                 ║" -ForegroundColor Red
Write-Host "  ╚══════════════════════════════════════════════════════════╝" -ForegroundColor Red
Write-Host ""

$confirm = Read-Host "  Type RESET to confirm, or press Enter to cancel"
if ($confirm -ne 'RESET') {
    Write-Host "  Cancelled." -ForegroundColor Gray
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
# logs.db — ask separately
# ─────────────────────────────────────────────────────────────────────────────
$clearLogs = $false
$logsAnswer = Read-Host "`n  Also clear logs.db? (task timings, worker errors, system stats) [y/N]"
if ($logsAnswer -match '^[Yy]') { $clearLogs = $true }

# ─────────────────────────────────────────────────────────────────────────────
# Verify DocVault server is not running
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Checking for running DocVault server"
try {
    $r = Invoke-RestMethod -Uri 'http://localhost:8000/api/workers/status' -TimeoutSec 2
    Write-Fail "DocVault appears to be running on port 8000."
    Write-Host "    Stop the server (Ctrl+C in its terminal) then re-run reset.ps1." -ForegroundColor Yellow
    exit 1
} catch {
    Write-OK "No DocVault server detected on port 8000"
}

# ─────────────────────────────────────────────────────────────────────────────
# Stop Qdrant
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Stopping Qdrant"
$qdrantContainer = 'docvault-qdrant-1'
$qdrantWasRunning = $false
try {
    $s = docker inspect $qdrantContainer --format '{{.State.Status}}' 2>&1
    if ($LASTEXITCODE -eq 0 -and $s.Trim() -eq 'running') {
        docker stop $qdrantContainer 2>&1 | Out-Null
        $qdrantWasRunning = $true
        Write-OK "Qdrant stopped"
    } else {
        Write-OK "Qdrant was not running"
    }
} catch {
    Write-Warn "Could not check Qdrant container — Docker may not be running"
}

# ─────────────────────────────────────────────────────────────────────────────
# Delete docvault.db
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Deleting docvault.db"
$dbPath = Join-Path $scriptDir 'docvault.db'
if (Test-Path $dbPath) {
    Remove-Item $dbPath -Force
    Write-OK "docvault.db deleted"
} else {
    Write-OK "docvault.db not found (already clean)"
}

# ─────────────────────────────────────────────────────────────────────────────
# Clear Qdrant docvault collection (delete storage for that collection only)
# The art_index collection lives in a separate subdirectory and is untouched.
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Clearing Qdrant docvault collection"
$qdrantStorage = Join-Path $scriptDir 'qdrant_storage'

# Qdrant stores each collection under: qdrant_storage/collections/<name>/
$docvaultCollectionPath = Join-Path $qdrantStorage 'collections\docvault'
if (Test-Path $docvaultCollectionPath) {
    Remove-Item $docvaultCollectionPath -Recurse -Force
    Write-OK "Removed qdrant_storage\collections\docvault"
} else {
    Write-OK "Qdrant docvault collection directory not found (already clean)"
}

# Also clear the WAL/alias entries that reference the docvault collection
# (Qdrant's raft state and aliases are safe to leave; they'll be recreated on next upsert)

# ─────────────────────────────────────────────────────────────────────────────
# Optionally clear logs.db
# ─────────────────────────────────────────────────────────────────────────────
if ($clearLogs) {
    Write-Step "Clearing logs.db"
    $logsPath = Join-Path $scriptDir 'logs.db'
    if (Test-Path $logsPath) {
        Remove-Item $logsPath -Force
        Write-OK "logs.db deleted (will be recreated on next startup)"
    } else {
        Write-OK "logs.db not found (already clean)"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Clear .cache extracted images (regenerable artefacts)
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Clearing cached extracted images"
$cachePath = 'E:\DocVault\.cache\extracted_images'
if (Test-Path $cachePath) {
    $count = (Get-ChildItem $cachePath -Recurse -File).Count
    Remove-Item $cachePath -Recurse -Force
    New-Item -ItemType Directory -Path $cachePath -Force | Out-Null
    Write-OK "Cleared $count cached files from .cache\extracted_images"
} else {
    Write-OK "Cache directory not found (already clean)"
}

# ─────────────────────────────────────────────────────────────────────────────
# Restart Qdrant
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Restarting Qdrant"
if ($qdrantWasRunning) {
    try {
        docker start $qdrantContainer 2>&1 | Out-Null
        # Wait for health
        $ready = $false
        for ($i = 0; $i -lt 20; $i++) {
            try {
                $r = Invoke-RestMethod -Uri 'http://localhost:6333/health' -TimeoutSec 2
                if ($r.status -eq 'ok') { $ready = $true; break }
            } catch {}
            Start-Sleep -Seconds 1
        }
        if ($ready) { Write-OK "Qdrant restarted and healthy" }
        else { Write-Warn "Qdrant restarted but health check timed out — check: docker logs $qdrantContainer" }
    } catch {
        Write-Warn "Could not restart Qdrant — run: docker start $qdrantContainer"
    }
} else {
    Write-OK "Qdrant was not running before reset — skipping restart"
    Write-Host "    Run .\start.ps1 when ready to start the system" -ForegroundColor Gray
}

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║  Reset complete. DocVault is on a clean slate.           ║" -ForegroundColor Green
Write-Host "  ║                                                          ║" -ForegroundColor Green
Write-Host "  ║  settings.db:  PRESERVED (config, keys, vault settings)  ║" -ForegroundColor Green
Write-Host "  ║  docvault.db:  DELETED (will recreate on next start)     ║" -ForegroundColor Green
Write-Host "  ║  Qdrant:       docvault collection cleared                ║" -ForegroundColor Green
Write-Host "  ║  art_index:    PRESERVED                                  ║" -ForegroundColor Green
if ($clearLogs) {
Write-Host "  ║  logs.db:      DELETED                                    ║" -ForegroundColor Green
} else {
Write-Host "  ║  logs.db:      PRESERVED (use -clearLogs to wipe)         ║" -ForegroundColor Green
}
Write-Host "  ╚══════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Run .\start.ps1 to begin re-ingestion." -ForegroundColor Cyan
Write-Host ""
