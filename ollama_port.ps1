# Ollama port selection helpers, dot-sourced by start.ps1.
# Kept in their own file so tests/test_start_port_selection.py can exercise them in
# isolation (start.ps1 runs everything at top level and can't be dot-sourced safely).
#
# Windows/Hyper-V (and WSL2's NAT) reserve dynamic TCP port-exclusion ranges
# that are re-randomised on every reboot. A port that's free today can start
# failing with "bind: An attempt was made to access a socket in a way
# forbidden by its access permissions" after the next reboot with zero
# warning - that's what took down port 11434 originally, and later 11600.
# Rather than trust a hardcoded port, verify it's still clear on every start
# and transparently move to a free one if it isn't.
#
# Callers must define Write-Warn before calling Find-FreeOllamaPort.

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

function Test-OllamaResponding {
    param([int]$Port)
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/version" -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Find-FreeOllamaPort {
    param([int]$Preferred)
    # A port that already answers as Ollama isn't "in use by something else" - it's the
    # healthy server we want to keep. Treating it as taken made every restart move Ollama
    # one port higher (11600 -> 11601 -> 11602 ...), rewriting config.ini and killing it.
    if (Test-OllamaResponding -Port $Preferred) { return $Preferred }
    $excluded = Get-ExcludedTcpRanges
    if (Test-PortFree -Port $Preferred -ExcludedRanges $excluded) { return $Preferred }
    Write-Warn "Port $Preferred is now inside a Windows/Hyper-V excluded range (or in use) - picking a new one"
    foreach ($candidate in 11600..11699) {
        if (Test-PortFree -Port $candidate -ExcludedRanges $excluded) { return $candidate }
    }
    return $null
}
