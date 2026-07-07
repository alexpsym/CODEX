[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Root,
    [Parameter(Mandatory = $true)] [string] $DecisionPath,
    [string] $BaseUrl = "http://127.0.0.1:8000",
    [string] $HealthUrl = "http://127.0.0.1:8000/health",
    [int] $ShutdownWaitSeconds = 12
)

$ErrorActionPreference = "Stop"

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
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 1
        return ($response.StatusCode -eq 200 -and (($response.Content | Out-String).Trim() -eq "ok"))
    }
    catch {
        return $false
    }
}

function Wait-HealthDown {
    param([int] $Seconds)

    for ($i = 0; $i -lt $Seconds; $i++) {
        if (-not (Test-HealthReady)) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return -not (Test-HealthReady)
}

function Stop-ListeningProcess {
    $connections = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
    $pids = @($connections | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { [int] $_ -gt 0 })
    if ($pids.Count -eq 0) {
        return $false
    }
    foreach ($processId in $pids) {
        Write-Host "[local-master] stopping stale process on port 8000: pid=$processId"
        Stop-Process -Id $processId -Force -ErrorAction Stop
    }
    return $true
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
    $body = @{ url = ($BaseUrl.TrimEnd("/") + "/") } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri ($BaseUrl.TrimEnd("/") + "/api/local-exit") -Method Post -Body $body -ContentType "application/json" -TimeoutSec 3 | Out-Null
    $exitSent = $true
    Write-Host "[local-master] requested graceful shutdown for stale dashboard server."
}
catch {
    Write-Host "[local-master] graceful stale-server shutdown was unavailable: $($_.Exception.Message)"
}

if ($exitSent -and (Wait-HealthDown -Seconds $ShutdownWaitSeconds)) {
    Write-Host "[local-master] stale dashboard server stopped; starting a fresh worker."
    Write-Decision "start"
    exit 0
}

try {
    if (Stop-ListeningProcess -and (Wait-HealthDown -Seconds $ShutdownWaitSeconds)) {
        Write-Host "[local-master] stale dashboard server was force-stopped; starting a fresh worker."
        Write-Decision "start"
        exit 0
    }
}
catch {
    Write-Host "[local-master] failed to force-stop stale dashboard server: $($_.Exception.Message)"
}

Write-Host "[local-master] ERROR: could not stop the stale dashboard server on port 8000."
exit 1
