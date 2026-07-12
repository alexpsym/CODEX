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
$script:LastHealthOkAt = $null
$script:LastAutostartOkAt = $null
$script:StartupCompleted = $false

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
        $ok = ($response.StatusCode -eq 200 -and (($response.Content | Out-String).Trim() -eq "ok"))
        if ($ok) {
            $script:LastHealthOkAt = Get-Date
        }
        return $ok
    }
    catch {
        return $false
    }
}

function Get-RequiredAutostartTargets {
    $raw = [string] $env:AUTOSTART_SCRIPTS
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $raw = "bybit_monitor,oanda_monitor,fxweekend-clone"
    }
    $tokens = @(
        $raw.Split(",") |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and $_.ToUpperInvariant() -notin @("NONE", "OFF", "DISABLED") }
    )
    if ($tokens.Count -eq 1 -and ($tokens[0] -eq "*" -or $tokens[0].ToUpperInvariant() -eq "ALL")) {
        return @("bybit_monitor", "oanda_monitor", "fxweekend-clone")
    }
    return @($tokens)
}

function Get-ScriptRowByName {
    param(
        [object[]] $Scripts,
        [string[]] $Names
    )

    foreach ($candidate in $Names) {
        $match = $Scripts | Where-Object { [string] $_.name -eq $candidate -or [string] $_.id -eq $candidate } | Select-Object -First 1
        if ($null -ne $match) {
            return $match
        }
    }
    return $null
}

function Select-FirstText {
    param([object[]] $Values)

    foreach ($value in $Values) {
        $text = [string] $value
        if (-not [string]::IsNullOrWhiteSpace($text)) {
            return $text
        }
    }
    return ""
}

function Get-AutostartTargetStatus {
    param(
        [object[]] $Scripts,
        [string] $Target
    )

    if ($Target -in @("bybit_monitor", "oanda_monitor")) {
        $monitor = Get-ScriptRowByName -Scripts $Scripts -Names @("monitor")
        if ($null -ne $monitor -and $null -ne $monitor.scanner_children) {
            $child = $monitor.scanner_children.$Target
            if ($null -ne $child) {
                $childDetail = Select-FirstText @($child.last_start_error, $child.last_exit_reason, "missing live scanner")
                return [pscustomobject]@{
                    Target = $Target
                    Ready = [bool] $child.running
                    Detail = if ($child.running) { "running" } else { $childDetail }
                }
            }
        }
    }

    $names = if ($Target -eq "fxweekend-clone") { @("fxweekend", "fxweekend-clone") } else { @($Target) }
    $row = Get-ScriptRowByName -Scripts $Scripts -Names $names
    if ($null -eq $row) {
        return [pscustomobject]@{ Target = $Target; Ready = $false; Detail = "configured target missing from /scripts" }
    }
    if ($Target -eq "fxweekend-clone") {
        $enabled = if ($null -ne $row.enabled) { [bool] $row.enabled } else { $true }
        $operational = if ($null -ne $row.operational) { [bool] $row.operational } else { ([bool] $row.running -and $enabled) }
        $detail = Select-FirstText @($row.status_detail, $(if ($operational) { "running" } else { "not operational" }))
        return [pscustomobject]@{ Target = $Target; Ready = $operational; Detail = $detail }
    }
    $fallbackDetail = if ($row.running) { "running" } else { "not running" }
    return [pscustomobject]@{
        Target = $Target
        Ready = [bool] $row.running
        Detail = Select-FirstText @($row.status_detail, $row.last_start_error, $row.last_exit_reason, $fallbackDetail)
    }
}

function Test-AutostartTargetsReady {
    try {
        $response = Invoke-RestMethod -Uri $ScriptsUrl -TimeoutSec 2
        $scripts = @($response)
        $targets = @(Get-RequiredAutostartTargets)
        if ($targets.Count -eq 0) {
            $script:LastAutostartOkAt = Get-Date
            return [pscustomobject]@{ Ready = $true; Missing = @(); Detail = "no autostart targets configured" }
        }
        $statuses = @($targets | ForEach-Object { Get-AutostartTargetStatus -Scripts $scripts -Target $_ })
        $missing = @($statuses | Where-Object { -not $_.Ready })
        if ($missing.Count -eq 0) {
            $script:LastAutostartOkAt = Get-Date
            return [pscustomobject]@{ Ready = $true; Missing = @(); Detail = ("targets ready: " + ($targets -join ", ")) }
        }
        $detail = ($missing | ForEach-Object { "{0} ({1})" -f $_.Target, $_.Detail }) -join "; "
        return [pscustomobject]@{ Ready = $false; Missing = $missing; Detail = $detail }
    }
    catch {
        return [pscustomobject]@{ Ready = $false; Missing = @(); Detail = "failed to query ${ScriptsUrl}: $($_.Exception.GetType().Name): $($_.Exception.Message)" }
    }
}

function Test-ScannerReady {
    return [bool] (Test-AutostartTargetsReady).Ready
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
$steadyCheckAt = Get-Date
$restartRecoveryActive = $false

while (-not $process.WaitForExit(1000)) {
    Write-WorkerLogTail
    $now = Get-Date
    $elapsed = [int][Math]::Floor(($now - $startedAt).TotalSeconds)

    if (-not $dashboardReady) {
        if (Test-DashboardHealth) {
            $dashboardReady = $true
            $scannerStartedAt = Get-Date
            $lastProgressAt = -100
            if ($restartRecoveryActive) {
                Write-LiveLine "[local-master] dashboard recovered after worker restart."
            }
            Write-StartupProgress -Phase "dashboard health is ready" -ElapsedSeconds $elapsed -TotalSeconds $MasterReadyTimeoutSeconds -Complete:$true
            Write-LiveLine "[local-master] startup step: waiting for configured autostart targets to report running."
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

    if (-not (Test-DashboardHealth)) {
        if (-not $restartRecoveryActive) {
            Write-LiveLine "[local-master] restart detected: dashboard health became unavailable while worker process stayed alive."
            Write-LiveLine "[local-master] waiting for recovery: dashboard health and configured autostart targets will be rechecked."
        }
        $restartRecoveryActive = $true
        $dashboardReady = $false
        $scannerReady = $false
        $scannerStartedAt = $null
        $lastProgressAt = -100
        $dashboardTimeoutReported = $false
        $scannerTimeoutReported = $false
        continue
    }

    if (-not $scannerReady) {
        $scannerElapsed = [int][Math]::Floor(($now - $scannerStartedAt).TotalSeconds)
        $autostartStatus = Test-AutostartTargetsReady
        if ($autostartStatus.Ready) {
            $scannerReady = $true
            if ($restartRecoveryActive) {
                Write-LiveLine "[local-master] configured autostart targets recovered: $($autostartStatus.Detail)"
                $restartRecoveryActive = $false
            }
            Write-StartupProgress -Phase "dashboard and scanner are ready; browser should open now" -ElapsedSeconds $ScannerReadyTimeoutSeconds -TotalSeconds $ScannerReadyTimeoutSeconds -Complete:$true
            Write-LiveLine "[local-master] startup complete. Live server log remains open below."
            $script:StartupCompleted = $true
            continue
        }

        if ($scannerElapsed -ge $ScannerReadyTimeoutSeconds -and -not $scannerTimeoutReported) {
            $scannerTimeoutReported = $true
            Write-LiveLine "[local-master] recovery failure: configured autostart target readiness has passed the usual $ScannerReadyTimeoutSeconds second window; detail: $($autostartStatus.Detail)"
        }

        if (($scannerElapsed -eq 0) -or (($elapsed - $lastProgressAt) -ge 5)) {
            Write-StartupProgress -Phase "checking configured autostart targets at $ScriptsUrl" -ElapsedSeconds $scannerElapsed -TotalSeconds $ScannerReadyTimeoutSeconds
            if (-not [string]::IsNullOrWhiteSpace([string] $autostartStatus.Detail)) {
                Write-LiveLine "[local-master] autostart readiness detail: $($autostartStatus.Detail)"
            }
            $lastProgressAt = $elapsed
        }
        continue
    }

    if (($now - $steadyCheckAt).TotalSeconds -ge 5) {
        $steadyCheckAt = $now
        $autostartStatus = Test-AutostartTargetsReady
        if (-not $autostartStatus.Ready) {
            Write-LiveLine "[local-master] autostart readiness lost after startup: $($autostartStatus.Detail)"
            Write-LiveLine "[local-master] waiting for recovery: configured autostart targets will be revalidated."
            $scannerReady = $false
            $scannerStartedAt = Get-Date
            $scannerTimeoutReported = $false
            $lastProgressAt = -100
        }
    }
}

$process.WaitForExit()
Write-WorkerLogTail
if ($script:PendingLogText.Length -gt 0) {
    Write-Host $script:PendingLogText
}
$finishedAt = Get-Date
$runtimeSeconds = [int][Math]::Floor(($finishedAt - $startedAt).TotalSeconds)
$normalMarkerPath = [string] $env:LOCAL_MASTER_NORMAL_EXIT_FILE
$normalMarkerExists = $false
if (-not [string]::IsNullOrWhiteSpace($normalMarkerPath)) {
    $normalMarkerExists = Test-Path -LiteralPath $normalMarkerPath -PathType Leaf
}
$uvicornExitLogged = $false
if (Test-Path -LiteralPath $script:WorkerLogPath -PathType Leaf) {
    try {
        $uvicornExitLogged = [bool] (Select-String -LiteralPath $script:WorkerLogPath -Pattern "uvicorn exited with" -SimpleMatch -Quiet)
    }
    catch {
        $uvicornExitLogged = $false
    }
}
Write-LiveLine ("[local-master] worker process ended: worker_pid={0} uvicorn_pid=unknown exit_code={1} runtime_seconds={2} last_health_ok_at={3} last_autostart_ok_at={4} normal_marker_exists={5}" -f $process.Id, $process.ExitCode, $runtimeSeconds, $(if ($script:LastHealthOkAt) { $script:LastHealthOkAt.ToString("o") } else { "never" }), $(if ($script:LastAutostartOkAt) { $script:LastAutostartOkAt.ToString("o") } else { "never" }), $normalMarkerExists)
if ($script:StartupCompleted -and -not $normalMarkerExists -and -not $uvicornExitLogged) {
    Write-LiveLine "[local-master] process disappeared before clean Uvicorn exit logging; classify this as external/forced termination unless the worker log shows another cause."
}
Start-Sleep -Milliseconds 100
exit $process.ExitCode
