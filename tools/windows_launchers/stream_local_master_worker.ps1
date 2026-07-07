[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Root,
    [Parameter(Mandatory = $true)] [string] $WorkerLog,
    [string] $HealthUrl = "http://127.0.0.1:8000/health",
    [string] $ScriptsUrl = "http://127.0.0.1:8000/scripts",
    [int] $MasterReadyTimeoutSeconds = 60,
    [int] $ScannerReadyTimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"

$script:WorkerLogPath = [IO.Path]::GetFullPath($WorkerLog)
$script:LogPosition = 0
$script:PendingLogText = ""

function Write-LiveLine {
    param([AllowNull()] [string] $Line)

    if ($null -eq $Line) {
        return
    }

    Write-Host $Line
}

function Write-WorkerLogTail {
    if (-not (Test-Path -LiteralPath $script:WorkerLogPath -PathType Leaf)) {
        return
    }

    $stream = $null
    $reader = $null
    try {
        $stream = [IO.File]::Open($script:WorkerLogPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        if ($stream.Length -lt $script:LogPosition) {
            $script:LogPosition = 0
            $script:PendingLogText = ""
        }
        [void] $stream.Seek($script:LogPosition, [IO.SeekOrigin]::Begin)
        $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::Default, $true)
        $newText = $reader.ReadToEnd()
        $script:LogPosition = $stream.Position
    }
    catch {
        return
    }
    finally {
        if ($reader) {
            $reader.Dispose()
        }
        elseif ($stream) {
            $stream.Dispose()
        }
    }

    if ([string]::IsNullOrEmpty($newText)) {
        return
    }

    $combined = $script:PendingLogText + $newText
    $parts = [regex]::Split($combined, "\r\n|\n|\r")
    if ($combined.EndsWith("`r") -or $combined.EndsWith("`n")) {
        $script:PendingLogText = ""
        $emitCount = $parts.Count
    }
    else {
        $script:PendingLogText = $parts[$parts.Count - 1]
        $emitCount = $parts.Count - 1
    }

    for ($i = 0; $i -lt $emitCount; $i++) {
        if ($parts[$i].Length -gt 0) {
            Write-Host $parts[$i]
        }
    }
}

function New-TextProgressBar {
    param(
        [int] $ElapsedSeconds,
        [int] $TotalSeconds,
        [bool] $Complete
    )

    $width = 24
    if ($Complete) {
        $filled = $width
    }
    else {
        $safeTotal = [Math]::Max(1, $TotalSeconds)
        $clampedElapsed = [Math]::Min($ElapsedSeconds, $safeTotal)
        $filled = [int][Math]::Floor(($clampedElapsed / $safeTotal) * ($width - 1))
        $filled = [Math]::Max(1, [Math]::Min($filled, $width - 1))
    }

    return "[{0}{1}]" -f ("#" * $filled), ("." * ($width - $filled))
}

function Write-StartupProgress {
    param(
        [string] $Phase,
        [int] $ElapsedSeconds,
        [int] $TotalSeconds,
        [bool] $Complete = $false
    )

    $safeTotal = [Math]::Max(1, $TotalSeconds)
    $percent = if ($Complete) { 100 } else { [Math]::Min(99, [int][Math]::Floor(($ElapsedSeconds / $safeTotal) * 100)) }
    $bar = New-TextProgressBar -ElapsedSeconds $ElapsedSeconds -TotalSeconds $TotalSeconds -Complete:$Complete
    Write-LiveLine ("[local-master] startup progress {0} {1,3}% {2}s/{3}s - {4}" -f $bar, $percent, $ElapsedSeconds, $TotalSeconds, $Phase)
}

function Test-DashboardHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 1
        return ($response.StatusCode -eq 200 -and (($response.Content | Out-String).Trim() -eq "ok"))
    }
    catch {
        return $false
    }
}

function Test-ScannerReady {
    try {
        $response = Invoke-RestMethod -Uri $ScriptsUrl -TimeoutSec 2
        if ($response -is [System.Array]) {
            $monitor = $response | Where-Object { $_.name -eq "monitor" } | Select-Object -First 1
        }
        else {
            $monitor = $null
        }
        return ($null -ne $monitor -and $monitor.running -eq $true)
    }
    catch {
        return $false
    }
}

$rootPath = [IO.Path]::GetFullPath($Root)
$workerScript = Join-Path $rootPath "run_local_master_control.bat"
if (-not (Test-Path -LiteralPath $workerScript -PathType Leaf)) {
    Write-LiveLine "[local-master] ERROR: worker batch file not found: $workerScript"
    exit 1
}

$logDir = Split-Path -Parent $script:WorkerLogPath
if ($logDir) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$cmdExe = if ($env:COMSPEC) { $env:COMSPEC } else { "cmd.exe" }
$processInfo = New-Object System.Diagnostics.ProcessStartInfo
$processInfo.FileName = $cmdExe
$processInfo.Arguments = ('/d /s /c ""{0}" __worker > "{1}" 2>&1"' -f $workerScript, $script:WorkerLogPath)
$processInfo.WorkingDirectory = $rootPath
$processInfo.UseShellExecute = $false
$processInfo.CreateNoWindow = $true
$processInfo.RedirectStandardInput = $true
$processInfo.RedirectStandardOutput = $false
$processInfo.RedirectStandardError = $false

$process = New-Object System.Diagnostics.Process
$process.StartInfo = $processInfo

Write-LiveLine "[local-master] live worker output starts now."
Write-StartupProgress -Phase "starting worker process" -ElapsedSeconds 0 -TotalSeconds $MasterReadyTimeoutSeconds

if (-not $process.Start()) {
    Write-LiveLine "[local-master] ERROR: failed to start worker process."
    exit 1
}
$process.StandardInput.Close()

$startedAt = Get-Date
$scannerStartedAt = $null
$dashboardReady = $false
$scannerReady = $false
$lastProgressAt = -100
$dashboardTimeoutReported = $false
$scannerTimeoutReported = $false

while (-not $process.WaitForExit(1000)) {
    Write-WorkerLogTail
    $now = Get-Date
    $elapsed = [int][Math]::Floor(($now - $startedAt).TotalSeconds)

    if (-not $dashboardReady) {
        if (Test-DashboardHealth) {
            $dashboardReady = $true
            $scannerStartedAt = Get-Date
            $lastProgressAt = -100
            Write-StartupProgress -Phase "dashboard health is ready" -ElapsedSeconds $elapsed -TotalSeconds $MasterReadyTimeoutSeconds -Complete:$true
            Write-LiveLine "[local-master] startup step: waiting for scanner/autostart monitor to report running."
            continue
        }

        if ($elapsed -ge $MasterReadyTimeoutSeconds -and -not $dashboardTimeoutReported) {
            $dashboardTimeoutReported = $true
            Write-LiveLine "[local-master] startup note: dashboard health has passed the usual $MasterReadyTimeoutSeconds second window; worker is still running, so the log continues below."
        }

        if (($elapsed -eq 1) -or (($elapsed - $lastProgressAt) -ge 5)) {
            Write-StartupProgress -Phase "checking dashboard health at $HealthUrl" -ElapsedSeconds $elapsed -TotalSeconds $MasterReadyTimeoutSeconds
            $lastProgressAt = $elapsed
        }
        continue
    }

    if (-not $scannerReady) {
        $scannerElapsed = [int][Math]::Floor(($now - $scannerStartedAt).TotalSeconds)
        if (Test-ScannerReady) {
            $scannerReady = $true
            Write-StartupProgress -Phase "dashboard and scanner are ready; browser should open now" -ElapsedSeconds $ScannerReadyTimeoutSeconds -TotalSeconds $ScannerReadyTimeoutSeconds -Complete:$true
            Write-LiveLine "[local-master] startup complete. Live server log remains open below."
            continue
        }

        if ($scannerElapsed -ge $ScannerReadyTimeoutSeconds -and -not $scannerTimeoutReported) {
            $scannerTimeoutReported = $true
            Write-LiveLine "[local-master] startup note: scanner readiness has passed the usual $ScannerReadyTimeoutSeconds second window; worker is still running, so the log continues below."
        }

        if (($scannerElapsed -eq 0) -or (($elapsed - $lastProgressAt) -ge 5)) {
            Write-StartupProgress -Phase "checking scanner/autostart monitor at $ScriptsUrl" -ElapsedSeconds $scannerElapsed -TotalSeconds $ScannerReadyTimeoutSeconds
            $lastProgressAt = $elapsed
        }
    }
}

$process.WaitForExit()
Write-WorkerLogTail
if ($script:PendingLogText.Length -gt 0) {
    Write-Host $script:PendingLogText
}
Start-Sleep -Milliseconds 100
exit $process.ExitCode
