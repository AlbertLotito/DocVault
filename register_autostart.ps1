#Requires -Version 5.1
<#
.SYNOPSIS
    Registers (or updates) the two Windows Scheduled Tasks that auto-start
    DocVault and its tray icon at logon. Safe to re-run any time (e.g.
    after moving the project to a new drive/path) - it updates existing
    tasks in place rather than erroring or duplicating.
.EXAMPLE
    .\register_autostart.ps1
#>

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "    [OK] $msg" -ForegroundColor Green }

function Register-OrUpdateTask {
    param(
        [string]$TaskName,
        [string]$Program,
        [string]$Arguments
    )
    $action    = New-ScheduledTaskAction -Execute $Program -Argument $Arguments -WorkingDirectory $scriptDir
    $trigger   = New-ScheduledTaskTrigger -AtLogOn
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings | Out-Null
        Write-OK "Updated existing task: $TaskName"
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
        Write-OK "Created task: $TaskName"
    }
}

Write-Step "Registering DocVault-Server task"
Register-OrUpdateTask -TaskName 'DocVault-Server' `
    -Program 'powershell.exe' `
    -Arguments "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptDir\start.ps1`""

Write-Step "Registering DocVault-Tray task"
$pythonw = Join-Path $scriptDir 'venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    throw "pythonw.exe not found at $pythonw - run setup.ps1 first."
}
Register-OrUpdateTask -TaskName 'DocVault-Tray' `
    -Program $pythonw `
    -Arguments "`"$scriptDir\tray\tray_app.py`""

Write-Host ""
Write-Host "  Done. Both tasks will run at next logon." -ForegroundColor Green
Write-Host "  To test now without logging off, run:" -ForegroundColor Green
Write-Host "    Start-ScheduledTask -TaskName 'DocVault-Server'" -ForegroundColor Gray
Write-Host "    Start-ScheduledTask -TaskName 'DocVault-Tray'" -ForegroundColor Gray
Write-Host ""
