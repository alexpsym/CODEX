[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Root,
    [string] $DecisionPath = "",
    [string] $BaseUrl = "http://127.0.0.1:8000",
    [string] $HealthUrl = "http://127.0.0.1:8000/health",
    [int] $ShutdownWaitSeconds = 12
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($DecisionPath)) {
    $DecisionPath = Join-Path ([IO.Path]::GetTempPath()) ("LocalTradingTools-preflight-{0}.txt" -f [Guid]::NewGuid().ToString("N"))
}

$buildFiles = @(
    "render/master_service.py",
    "render/static/calculator.js",
    "render/static/instrument_lookup.js",
    "render/static/open_orders.js",
    "render/static/trading_journal.js",
    "tools/master_journal_workbook.py",
    "run_local_master_control.bat",
    "tools/windows_launchers/local_master_worker_console.bat",
    "tools/windows_launchers/stream_local_master_worker.ps1",
    "tools/windows_launchers/ensure_local_master_server.ps1"
)

function Write-Decision {
    param([string] $Value)

    $decisionFull = [IO.Path]::GetFullPath($DecisionPath)
    $decisionDir = Split-Path -Parent $decisionFull
    if ($decisionDir) {
        New-Item -ItemType Directory -Path $decisionDir -Force | Out-Null
    }
    Set-Content -LiteralPath $decisionFull -Value $Value -NoNewline -Encoding ASCII
}

function Get-LocalSourceStamp {
    param([string] $RootPath)

    $rootFull = [IO.Path]::GetFullPath($RootPath)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($rel in $buildFiles) {
            $normalized = $rel.Replace("\", "/")
            $relBytes = [Text.Encoding]::UTF8.GetBytes($normalized + "`n")
            [void] $sha.TransformBlock($relBytes, 0, $relBytes.Length, $null, 0)
            $path = Join-Path $rootFull $normalized
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $bytes = [IO.File]::ReadAllBytes($path)
            }
            else {
                $bytes = [Text.Encoding]::UTF8.GetBytes("<missing>")
            }
            [void] $sha.TransformBlock($bytes, 0, $bytes.Length, $null, 0)
            $newline = [Text.Encoding]::UTF8.GetBytes("`n")
            [void] $sha.TransformBlock($newline, 0, $newline.Length, $null, 0)
        }
        $emptyBytes = New-Object byte[] 0
        [void] $sha.TransformFinalBlock($emptyBytes, 0, 0)
        return ([BitConverter]::ToString($sha.Hash).Replace("-", "").ToLowerInvariant()).Substring(0, 16)
    }
    finally {
        $sha.Dispose()
    }
}

function Test-HealthReady {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl -and $curl.Path) {
        try {
            $content = & $curl.Path -s -m 2 $HealthUrl
            return (($LASTEXITCODE -eq 0) -and ([string] $content).Trim() -eq "ok")
        }
        catch {
        }
    }

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 1
        return ($response.StatusCode -eq 200 -and (($response.Content | Out-String).Trim() -eq "ok"))
    }
    catch {
        return $false
    }
}

function Test-PortListening {
    try {
        $lines = @(& netstat.exe -ano -p tcp 2>$null)
        foreach ($line in $lines) {
            if ($line -match "^\s*TCP\s+\S+:8000\s+\S+\s+LISTENING\s+(\d+)\s*$") {
                return $true
            }
        }
    }
    catch {
    }

    try {
        $connections = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
        return ($connections.Count -gt 0)
    }
    catch {
        return $false
    }
}

function Wait-HealthDown {
    param([int] $Seconds)

    for ($i = 0; $i -lt $Seconds; $i++) {
        if ((-not (Test-HealthReady)) -and (-not (Test-PortListening))) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return ((-not (Test-HealthReady)) -and (-not (Test-PortListening)))
}

function Stop-ListeningProcess {
    param([string] $RootPath)

    $pids = @()
    try {
        $lines = @(& netstat.exe -ano -p tcp 2>$null)
        foreach ($line in $lines) {
            if ($line -match "^\s*TCP\s+\S+:8000\s+\S+\s+LISTENING\s+(\d+)\s*$") {
                $pids += [int] $Matches[1]
            }
        }
    }
    catch {
    }

    if ($pids.Count -eq 0) {
        $connections = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
        $pids = @($connections | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { [int] $_ -gt 0 })
    }

    $pids = @($pids | Sort-Object -Unique | Where-Object { [int] $_ -gt 0 })
    if ($pids.Count -eq 0) {
        return $false
    }
    foreach ($processId in $pids) {
        Stop-LocalMasterWorkerSupervisor -ListenerProcessId $processId -RootPath $RootPath | Out-Null
        Write-Host "[local-master] stopping existing process on port 8000: pid=$processId"
        Stop-Process -Id $processId -Force -ErrorAction Stop
    }
    return $true
}

function Stop-LocalMasterWorkerSupervisor {
    param(
        [int] $ListenerProcessId,
        [string] $RootPath
    )

    try {
        $child = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ListenerProcessId) -ErrorAction Stop
        if ($null -eq $child -or [int] $child.ParentProcessId -le 0) {
            return $false
        }
        $parentProcessId = [int] $child.ParentProcessId
        if ($parentProcessId -eq $PID) {
            return $false
        }
        $parent = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $parentProcessId) -ErrorAction Stop
        if ($null -eq $parent) {
            return $false
        }
        $commandLine = [string] $parent.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            return $false
        }
        $expectedWorkerScript = [IO.Path]::GetFullPath((Join-Path $RootPath "run_local_master_control.bat"))
        $normalizedCommand = $commandLine.Replace("/", "\").ToLowerInvariant()
        $normalizedScript = $expectedWorkerScript.Replace("/", "\").ToLowerInvariant()
        if ($normalizedCommand.Contains($normalizedScript) -and $normalizedCommand.Contains("__worker")) {
            Write-Host "[local-master] stopping existing worker supervisor: pid=$parentProcessId"
            Stop-Process -Id $parentProcessId -Force -ErrorAction Stop
            return $true
        }
    }
    catch {
        return $false
    }
    return $false
}

$rootFull = [IO.Path]::GetFullPath($Root)
$expectedStamp = Get-LocalSourceStamp -RootPath $rootFull

if (-not (Test-HealthReady)) {
    Write-Host "[local-master] no existing dashboard server detected; starting a fresh worker."
    Write-Decision "start"
    exit 0
}

Write-Host "[local-master] existing dashboard server detected; checking source version."
$buildInfo = $null
try {
    $buildInfo = Invoke-RestMethod -Uri ($BaseUrl.TrimEnd("/") + "/api/local-build-info") -TimeoutSec 2
}
catch {
    $buildInfo = $null
}

$serverStamp = ""
$serverRoot = ""
if ($null -ne $buildInfo) {
    if ($null -ne $buildInfo.source_stamp) {
        $serverStamp = [string] $buildInfo.source_stamp
    }
    if ($null -ne $buildInfo.root) {
        $serverRoot = [string] $buildInfo.root
    }
}
$serverRootFull = if ([string]::IsNullOrWhiteSpace($serverRoot)) { "" } else { try { [IO.Path]::GetFullPath($serverRoot) } catch { $serverRoot } }

$slashChar = [char] 92
if ($serverStamp -eq $expectedStamp -and $serverRootFull.TrimEnd($slashChar) -ieq $rootFull.TrimEnd($slashChar)) {
    Write-Host "[local-master] existing dashboard server matches this checkout; restarting it so this launch owns the worker and browser."
}
elseif ([string]::IsNullOrWhiteSpace($serverStamp)) {
    Write-Host "[local-master] existing dashboard server has no build-info endpoint; treating it as stale."
}
else {
    Write-Host "[local-master] existing dashboard server is stale: server=$serverStamp expected=$expectedStamp"
}

$exitSent = $false
try {
    $body = @{ reason = "launcher_preflight" } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri ($BaseUrl.TrimEnd("/") + "/api/local-shutdown") -Method Post -Body $body -ContentType "application/json" -TimeoutSec 3 | Out-Null
    $exitSent = $true
    Write-Host "[local-master] requested controlled shutdown for existing dashboard server."
}
catch {
    Write-Host "[local-master] controlled shutdown was unavailable: $($_.Exception.Message)"
}

if ($exitSent -and (Wait-HealthDown -Seconds $ShutdownWaitSeconds)) {
    Write-Host "[local-master] existing dashboard server stopped; starting a fresh worker."
    Write-Decision "start"
    exit 0
}

try {
    $body = @{ url = ($BaseUrl.TrimEnd("/") + "/") } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri ($BaseUrl.TrimEnd("/") + "/api/local-exit") -Method Post -Body $body -ContentType "application/json" -TimeoutSec 3 | Out-Null
    $exitSent = $true
    Write-Host "[local-master] requested graceful shutdown for existing dashboard server."
}
catch {
    Write-Host "[local-master] graceful shutdown was unavailable: $($_.Exception.Message)"
}

if ($exitSent -and (Wait-HealthDown -Seconds $ShutdownWaitSeconds)) {
    Write-Host "[local-master] existing dashboard server stopped; starting a fresh worker."
    Write-Decision "start"
    exit 0
}

try {
    if ((Stop-ListeningProcess -RootPath $rootFull) -and (Wait-HealthDown -Seconds $ShutdownWaitSeconds)) {
        Write-Host "[local-master] existing dashboard server was force-stopped; starting a fresh worker."
        Write-Decision "start"
        exit 0
    }
}
catch {
    Write-Host "[local-master] failed to force-stop existing dashboard server: $($_.Exception.Message)"
}

Write-Host "[local-master] ERROR: could not stop the existing dashboard server on port 8000."
exit 1
