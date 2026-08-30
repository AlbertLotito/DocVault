#Requires -Version 5.1
<#
.SYNOPSIS
    One-time DocVault setup. Creates the venv, writes config.ini, and pulls
    Ollama models. Re-running is safe - it skips steps already done.
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

# ---
# 1. Python version check
# ---
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

# ---
# 2. Virtual environment
# ---
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

# ---
# 3. Configuration
# ---
Write-Step "Configuration (config.ini)"

$configPath = Join-Path $scriptDir 'config.ini'

# Defaults are the current production values on this machine.
# Change them here if you're setting up on a new machine.
$defaults = @{
    scan_directory      = 'E:\DocTest'
    cache_directory     = '.cache\extracted_images'
    sqlite_path         = 'docvault.db'
    tesseract_path      = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
    poppler_path        = 'bin\poppler\Library\bin'
    server_host         = '127.0.0.1'
    server_port         = '8050'
    ollama_host         = 'http://localhost:11600'
    ollama_chat_model   = 'qwen2.5:14b'
    ollama_embed_model  = 'nomic-embed-text'
    ollama_vision_model = 'minicpm-v'
    lancedb_path        = 'lancedb_storage'
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

$cacheDir     = $defaults.cache_directory
$sqlitePath   = $defaults.sqlite_path
$serverHost   = $defaults.server_host
$serverPort   = $defaults.server_port
$lancedbPath  = $defaults.lancedb_path

# Write config.ini
# Note: start.ps1 re-verifies the Ollama port on every run and rewrites
# [ollama] host here automatically if Windows' dynamic port-exclusion ranges
# (which shift on reboot) collide with whatever is written below.
$configContent = @"
[server]
host = $serverHost
port = $serverPort

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

[lancedb]
path = $lancedbPath

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

# Set-Content -Encoding UTF8 writes a BOM, which Python's configparser
# (core/settings.py) chokes on with "File contains no section headers".
# -Encoding utf8NoBOM only exists on PowerShell 6+; this .NET call works
# on both 5.1 and 7+ (script requires 5.1).
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($configPath, $configContent, $utf8NoBom)
Write-OK "config.ini written"

# ---
# 4. Ollama models
# ---
Write-Step "Pulling Ollama models"
$ollamaApiBase = $ollamaHost -replace '/+$', ''
# `ollama pull`/`ollama list` read OLLAMA_HOST from the environment, not from
# config.ini - set it for this process so the CLI targets the same instance
# config.ini points at, rather than whatever port happens to be persisted.
$env:OLLAMA_HOST = ($ollamaApiBase -replace '^https?://', '')
$ollamaRunning = $false
try {
    Invoke-RestMethod -Uri "$ollamaApiBase/api/version" -TimeoutSec 3 | Out-Null
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
        $tags = (Invoke-RestMethod -Uri "$ollamaApiBase/api/tags" -TimeoutSec 5).models.name
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

# ---
# 5. Tesseract sanity check
# ---
Write-Step "Checking Tesseract"
if (Test-Path $tesseract) {
    Write-OK "Found at $tesseract"
} else {
    Write-Warn "Not found at $tesseract"
    Write-Host "    Download from: https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Gray
    Write-Host "    Then update config.ini [tesseract] path= to point to tesseract.exe" -ForegroundColor Gray
}

# ---
# Done
# ---
Write-Host ""
Write-Host "  Setup complete. Run .\start.ps1 to launch DocVault." -ForegroundColor Green
Write-Host ""
