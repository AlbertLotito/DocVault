#Requires -Version 5.1
<#
.SYNOPSIS
    One-time DocVault setup. Creates the venv, writes config.ini, pulls Ollama
    models, and starts Qdrant. Re-running is safe — it skips steps already done.
.EXAMPLE
    .\setup.ps1
#>

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step  { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "    [!!] $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "    [XX] $msg" -ForegroundColor Red }

function Prompt-With-Default {
    param([string]$Label, [string]$Default)
    $answer = Read-Host "    $Label [`"$Default`"]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim()
}

Write-Host ""
Write-Host "  DocVault Setup" -ForegroundColor Cyan
Write-Host "  Press Enter to accept defaults (current production values)" -ForegroundColor Gray
Write-Host ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. Python version check
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Checking Python"
try {
    $pyVer = (python --version 2>&1).ToString().Trim()
    Write-OK "$pyVer"
    if ($pyVer -notmatch '3\.(1[1-9]|[2-9]\d)') {
        Write-Warn "Python 3.11+ recommended. Found: $pyVer"
    }
} catch {
    Write-Fail "Python not found in PATH. Install Python 3.11+ and re-run."
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Virtual environment
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Python virtual environment"
$venvPath = Join-Path $scriptDir 'venv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'

if (Test-Path $venvPython) {
    Write-OK "venv already exists"
} else {
    Write-Host "    Creating venv..." -ForegroundColor Gray
    python -m venv $venvPath
    Write-OK "venv created"
}

Write-Host "    Installing / updating requirements..." -ForegroundColor Gray
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $scriptDir 'requirements.txt') --quiet
Write-OK "Dependencies installed"

# ─────────────────────────────────────────────────────────────────────────────
# 3. Configuration
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Configuration (config.ini)"

$configPath = Join-Path $scriptDir 'config.ini'

# Defaults are the current production values on this machine.
# Change them here if you're setting up on a new machine.
$defaults = @{
    scan_directory      = 'E:\DocTest'
    cache_directory     = 'E:\DocVault\.cache\extracted_images'
    sqlite_path         = 'E:\DocVault\docvault.db'
    tesseract_path      = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
    poppler_path        = 'E:\DocVault\bin\poppler\Library\bin'
    ollama_host         = 'http://localhost:11434'
    ollama_chat_model   = 'deepseek-r1:14b'
    ollama_embed_model  = 'nomic-embed-text'
    ollama_vision_model = 'minicpm-v'
    qdrant_host         = 'localhost'
    qdrant_port         = '6333'
}

Write-Host "    Scan directory (primary vault root):"
$scanDir      = Prompt-With-Default "  Scan directory"      $defaults.scan_directory

Write-Host "    Tool paths:"
$tesseract    = Prompt-With-Default "  Tesseract .exe"      $defaults.tesseract_path
$poppler      = Prompt-With-Default "  Poppler bin dir"     $defaults.poppler_path

Write-Host "    Ollama:"
$ollamaHost   = Prompt-With-Default "  Ollama host"         $defaults.ollama_host
$chatModel    = Prompt-With-Default "  Chat model"          $defaults.ollama_chat_model
$embedModel   = Prompt-With-Default "  Embed model"         $defaults.ollama_embed_model
$visionModel  = Prompt-With-Default "  Vision model"        $defaults.ollama_vision_model

Write-Host "    Qdrant:"
$qdrantHost   = Prompt-With-Default "  Qdrant host"         $defaults.qdrant_host
$qdrantPort   = Prompt-With-Default "  Qdrant port"         $defaults.qdrant_port

$cacheDir     = $defaults.cache_directory
$sqlitePath   = $defaults.sqlite_path

# Write config.ini
$configContent = @"
[paths]
scan_directory = $scanDir
cache_directory = $cacheDir

[database]
sqlite_path = $sqlitePath

[llm]
provider = ollama

[tesseract]
path = $tesseract

[ollama]
host = $ollamaHost
chat_model = $chatModel
embed_model = $embedModel
num_ctx = 8192
temperature = 0.1
num_predict = -1
top_p = 0.9
repeat_penalty = 1.1

[qdrant]
host = $qdrantHost
port = $qdrantPort

[pdf]
poppler_path = $poppler
sparse_threshold = 50

[vision]
model = $visionModel
describe_images = true

[embeddings]
chunk_size = 600
chunk_overlap = 100
score_threshold = 0.65

[search]
rag_top_k = 5
rag_threshold = 0.50
result_limit = 20
fts_weight = 0.4
sem_weight = 0.6

[video]
describe_frames = true
frame_interval = 10

[google]
credentials_path = $scriptDir\credentials\google_credentials.json
token_path = $scriptDir\credentials\google_token.json
"@

Set-Content -Path $configPath -Value $configContent -Encoding UTF8
Write-OK "config.ini written"

# ─────────────────────────────────────────────────────────────────────────────
# 4. One-time cleanup: remove orphaned docvault-app containers
#    (from a previous docker-compose attempt for the app itself)
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Cleaning up orphaned containers"
$orphans = @('hopeful_edison', 'strange_payne', 'docvault-docvault-1')
foreach ($name in $orphans) {
    $exists = docker inspect $name 2>&1
    if ($LASTEXITCODE -eq 0) {
        docker rm -f $name 2>&1 | Out-Null
        Write-OK "Removed $name"
    }
}
Write-OK "Cleanup done"

# ─────────────────────────────────────────────────────────────────────────────
# 5. Docker check
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Checking Docker"
try {
    docker version 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw }
    Write-OK "Docker daemon running"
} catch {
    Write-Warn "Docker is not running — skipping Qdrant setup."
    Write-Warn "Start Docker Desktop, then run start.ps1 to continue."
    $skipQdrant = $true
}

# ─────────────────────────────────────────────────────────────────────────────
# 6. Qdrant
# ─────────────────────────────────────────────────────────────────────────────
if (-not $skipQdrant) {
    Write-Step "Starting Qdrant"
    $qdrantContainer = 'docvault-qdrant-1'
    $containerExists = $false
    try {
        docker inspect $qdrantContainer 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $containerExists = $true }
    } catch {}

    if ($containerExists) {
        # Apply restart policy in case it's missing
        docker update --restart=unless-stopped $qdrantContainer 2>&1 | Out-Null
        $s = docker inspect $qdrantContainer --format '{{.State.Status}}' 2>&1
        if ($s.Trim() -ne 'running') {
            docker start $qdrantContainer 2>&1 | Out-Null
        }
        Write-OK "Qdrant container started (restart=unless-stopped applied)"
    } else {
        Write-Host "    Creating Qdrant container via docker compose..." -ForegroundColor Gray
        Push-Location $scriptDir
        docker compose up -d qdrant 2>&1
        Pop-Location
        Write-OK "Qdrant container created"
    }

    # Wait for health
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $r = Invoke-RestMethod -Uri 'http://localhost:6333/health' -TimeoutSec 2
            if ($r.status -eq 'ok') { $ready = $true; break }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if ($ready) { Write-OK "Qdrant healthy" }
    else { Write-Warn "Qdrant did not respond after 30s — check: docker logs $qdrantContainer" }
}

# ─────────────────────────────────────────────────────────────────────────────
# 7. Ollama models
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Pulling Ollama models"
$ollamaRunning = $false
try {
    Invoke-RestMethod -Uri 'http://localhost:11434/api/version' -TimeoutSec 3 | Out-Null
    $ollamaRunning = $true
} catch {}

if (-not $ollamaRunning) {
    Write-Warn "Ollama is not running. Start it (`ollama serve`) then pull models manually:"
    Write-Host "    ollama pull $embedModel" -ForegroundColor Gray
    Write-Host "    ollama pull $visionModel" -ForegroundColor Gray
    Write-Host "    ollama pull $chatModel" -ForegroundColor Gray
} else {
    $pullModels = @($embedModel, $visionModel, $chatModel)
    try {
        $tags = (Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -TimeoutSec 5).models.name
    } catch { $tags = @() }

    foreach ($m in $pullModels) {
        $found = $tags | Where-Object { $_ -eq $m -or $_ -eq "$m`:latest" }
        if ($found) {
            Write-OK "$m already pulled"
        } else {
            Write-Host "    Pulling $m (this may take a while)..." -ForegroundColor Gray
            ollama pull $m
            Write-OK "$m pulled"
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 8. Tesseract sanity check
# ─────────────────────────────────────────────────────────────────────────────
Write-Step "Checking Tesseract"
if (Test-Path $tesseract) {
    Write-OK "Found at $tesseract"
} else {
    Write-Warn "Not found at $tesseract"
    Write-Host "    Download from: https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Gray
    Write-Host "    Then update config.ini [tesseract] path= to point to tesseract.exe" -ForegroundColor Gray
}

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Setup complete. Run .\start.ps1 to launch DocVault." -ForegroundColor Green
Write-Host ""
