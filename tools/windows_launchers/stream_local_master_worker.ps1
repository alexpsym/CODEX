[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Root,
    [Parameter(Mandatory = $true)] [string] $WorkerLog,
    [string] $HealthUrl = "http://127.0.0.1:8000/health",
    [string] $ReadinessUrl = "http://127.0.0.1:8000/api/startup-readiness",
    [string] $ScriptsUrl = "http://127.0.0.1:8000/scripts",
    [int] $MasterReadyTimeoutSeconds = 60,
    [int] $ScannerReadyTimeoutSeconds = 90,
    [int] $HealthFailureThreshold = 3
)

$ErrorActionPreference = "Stop"

$script:WorkerLogPath = [IO.Path]::GetFullPath($WorkerLog)
$script:LogPosition = 0
$script:PendingLogText = ""
$script:LastHealthOkAt = $null
$script:LastAutostartOkAt = $null
$script:StartupCompleted = $false
$script:BackgroundReady = $null
$script:LastBackgroundDetail = ""
$script:CanonicalAutostartTargetsKnown = $false
$script:CanonicalAutostartTargets = @()

function New-UvicornGenerationEvidence {
    param(
        [AllowNull()] [object] $Generation = $null,
        [bool] $StartLogged = $false,
        [AllowNull()] [string] $StartLine = ""
    )

    return [pscustomobject]@{
        Generation = $Generation
        StartLogged = $StartLogged
        StartLine = [string] $StartLine
        ExitLogged = $false
        ExitCode = $null
        ExitLine = ""
    }
}

function Get-LatestUvicornGenerationEvidence {
    param([string] $LogPath)

    $evidence = New-UvicornGenerationEvidence
    $pendingStartLine = ""
    if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        return $evidence
    }

    try {
        foreach ($line in Get-Content -LiteralPath $LogPath -ErrorAction Stop) {
            $text = [string] $line
            if ($text -match "starting uvicorn") {
                $pendingStartLine = $text
                if ($null -eq $evidence.Generation -or $evidence.ExitLogged) {
                    $evidence = New-UvicornGenerationEvidence -StartLogged $true -StartLine $text
                }
                else {
                    $evidence.StartLogged = $true
                    $evidence.StartLine = $text
                }
                continue
            }

            if ($text -match "uvicorn restart generation\s+([0-9]+)") {
                $startLine = $text
                if (-not [string]::IsNullOrWhiteSpace($pendingStartLine)) {
                    $startLine = $pendingStartLine
                }
                $evidence = New-UvicornGenerationEvidence -Generation ([int] $Matches[1]) -StartLogged $true -StartLine $startLine
                $pendingStartLine = ""
                continue
            }

            if ($text -match "uvicorn exited with\s+(-?[0-9]+)") {
                if ($null -eq $evidence.Generation -and -not $evidence.StartLogged) {
                    $evidence = New-UvicornGenerationEvidence
                }
                $evidence.ExitLogged = $true
                $evidence.ExitCode = [int] $Matches[1]
                $evidence.ExitLine = $text
                $pendingStartLine = ""
            }
        }
    }
    catch {
        return $evidence
    }

    return $evidence
}

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
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl -and $curl.Path) {
        try {
            $content = & $curl.Path -s --noproxy "*" -m 2 $HealthUrl
            $ok = (($LASTEXITCODE -eq 0) -and ([string] $content).Trim() -eq "ok")
            if ($ok) {
                $script:LastHealthOkAt = Get-Date
                return $true
            }
        }
        catch {
        }
    }

    try {
        $response = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
        $ok = (([string] $response).Trim() -eq "ok")
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
        $raw = "bybit_monitor,oanda_monitor"
    }
    $tokens = @(
        $raw.Split(",") |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and $_.ToUpperInvariant() -notin @("NONE", "OFF", "DISABLED") }
    )
    if ($tokens.Count -eq 1 -and ($tokens[0] -eq "*" -or $tokens[0].ToUpperInvariant() -eq "ALL")) {
        $tokens = @("bybit_monitor", "oanda_monitor")
    }
    $excluded = @(
        ([string] $env:AUTOSTART_EXCLUDE).Split(",") |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
    return @(
        $tokens |
            Where-Object {
                $_ -notin $excluded -and
                $_ -notin @("fxweekend", "fxweekend-clone")
            }
    )
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
                $childDetail = Select-FirstText @($child.last_start_error, $child.last_exit_reason, "missing live alert monitor")
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
        $targets = if ($script:CanonicalAutostartTargetsKnown) {
            @($script:CanonicalAutostartTargets)
        }
        else {
            @(Get-RequiredAutostartTargets)
        }
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

function ConvertTo-BackgroundWarningText {
    param([AllowNull()] [object] $Warning)

    if ($null -eq $Warning) {
        return ""
    }
    if ($Warning -is [string]) {
        return ([string] $Warning).Trim()
    }

    $name = Select-FirstText @($Warning.name, $Warning.target, $Warning.component, $Warning.label)
    $reason = Select-FirstText @($Warning.reason, $Warning.detail, $Warning.message, $Warning.phase)
    if ($name -and $reason) {
        return "{0} ({1})" -f $name, $reason
    }
    return (Select-FirstText @($reason, $name, ($Warning | ConvertTo-Json -Compress -Depth 4)))
}

function Get-CoreStartupReadiness {
    try {
        $response = Invoke-RestMethod -Uri $ReadinessUrl -TimeoutSec 2
        $autostartTargetsProperty = $response.PSObject.Properties["autostart_targets"]
        if ($null -ne $autostartTargetsProperty) {
            $script:CanonicalAutostartTargets = @(
                $response.autostart_targets |
                    ForEach-Object { ([string] $_).Trim() } |
                    Where-Object { $_ }
            )
            $script:CanonicalAutostartTargetsKnown = $true
        }
        $coreReadyProperty = $response.PSObject.Properties["core_ready"]
        $coreReady = if ($null -ne $coreReadyProperty) { [bool] $response.core_ready } else { [bool] $response.ready }
        $backgroundReadyProperty = $response.PSObject.Properties["background_ready"]
        $backgroundReady = if ($null -ne $backgroundReadyProperty) { [bool] $response.background_ready } else { $null }
        $warnings = @()
        if ($null -ne $response.background_warnings) {
            $warnings += @($response.background_warnings | ForEach-Object { ConvertTo-BackgroundWarningText $_ })
        }
        if ($warnings.Count -eq 0 -and -not [string]::IsNullOrWhiteSpace([string] $response.background_warning)) {
            $warnings += [string] $response.background_warning
        }
        if ($warnings.Count -eq 0 -and $null -ne $response.components) {
            $warnings += @(
                $response.components |
                    Where-Object {
                        [string] $_.name -ne "state_restore" -and
                        $_.blocking -eq $false -and
                        $_.ready -eq $false
                    } |
                    ForEach-Object { ConvertTo-BackgroundWarningText $_ }
            )
        }
        $warnings = @($warnings | Where-Object { -not [string]::IsNullOrWhiteSpace([string] $_) } | Select-Object -Unique)
        $phase = Select-FirstText @($response.startup_phase, $(if ($coreReady) { "ready" } else { "checking" }))
        $component = [string] $response.blocking_component
        $reason = [string] $response.failure_reason
        $detailParts = @("phase=$phase")
        if (-not [string]::IsNullOrWhiteSpace($component)) {
            $detailParts += "component=$component"
        }
        if (-not [string]::IsNullOrWhiteSpace($reason)) {
            $detailParts += "reason=$reason"
        }
        return [pscustomobject]@{
            Available = $true
            Ready = $coreReady
            Phase = $phase
            BlockingComponent = $component
            FailureReason = $reason
            Detail = $detailParts -join "; "
            BackgroundReady = $backgroundReady
            BackgroundDetail = $warnings -join "; "
        }
    }
    catch {
        return [pscustomobject]@{
            Available = $false
            Ready = $false
            Phase = "readiness_unavailable"
            BlockingComponent = "startup_readiness"
            FailureReason = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
            Detail = "failed to query ${ReadinessUrl}: $($_.Exception.GetType().Name): $($_.Exception.Message)"
            BackgroundReady = $null
            BackgroundDetail = ""
        }
    }
}

function Update-BackgroundReadiness {
    param(
        [AllowNull()] [object] $CoreStatus,
        [AllowNull()] [object] $AutostartStatus
    )

    $apiHasStatus = ($null -ne $CoreStatus -and $null -ne $CoreStatus.BackgroundReady)
    $apiReady = if ($apiHasStatus) { [bool] $CoreStatus.BackgroundReady } else { $true }
    $scriptsHaveStatus = $null -ne $AutostartStatus
    $scriptsReady = if ($scriptsHaveStatus) { [bool] $AutostartStatus.Ready } else { $true }
    $ready = $apiReady -and $scriptsReady
    $detail = Select-FirstText @(
        $(if ($apiHasStatus -and -not $apiReady) { $CoreStatus.BackgroundDetail } else { "" }),
        $(if ($scriptsHaveStatus -and -not $scriptsReady) { $AutostartStatus.Detail } else { "" }),
        $(if ($ready -and $scriptsHaveStatus) { $AutostartStatus.Detail } else { "" }),
        $(if ($ready) { "background services ready" } else { "background service readiness unavailable" })
    )

    if ($ready) {
        $script:LastAutostartOkAt = Get-Date
        if ($script:BackgroundReady -eq $false) {
            Write-LiveLine "[local-master] configured autostart targets recovered: $detail"
        }
    }
    elseif ($script:BackgroundReady -ne $false -or $detail -ne $script:LastBackgroundDetail) {
        if ($script:BackgroundReady -eq $true) {
            Write-LiveLine "[local-master] autostart readiness lost after startup: $detail (nonblocking; startup progress remains complete)."
        }
        else {
            Write-LiveLine "[local-master] background startup warning (nonblocking): $detail"
        }
        Write-LiveLine "[local-master] background services remain supervised and will continue retrying."
    }

    $script:BackgroundReady = $ready
    $script:LastBackgroundDetail = $detail
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
$coreStartedAt = $null
$dashboardReady = $false
$coreReady = $false
$lastProgressAt = -100
$dashboardTimeoutReported = $false
$coreTimeoutReported = $false
$steadyCheckAt = Get-Date
$restartRecoveryActive = $false
$lastCoreDetail = ""
$lastReadinessQueryWarning = ""
$consecutiveHealthFailures = 0

while (-not $process.WaitForExit(1000)) {
    Write-WorkerLogTail
    $now = Get-Date
    $elapsed = [int][Math]::Floor(($now - $startedAt).TotalSeconds)

    if (-not $dashboardReady) {
        if (Test-DashboardHealth) {
            $dashboardReady = $true
            $consecutiveHealthFailures = 0
            $coreStartedAt = Get-Date
            $lastProgressAt = -100
            if ($restartRecoveryActive) {
                Write-LiveLine "[local-master] dashboard recovered after worker restart."
            }
            Write-LiveLine "[local-master] dashboard health is ready; checking required core state via $ReadinessUrl."
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

    $dashboardHealthOk = Test-DashboardHealth
    if (-not $dashboardHealthOk) {
        $consecutiveHealthFailures += 1
        if ($consecutiveHealthFailures -lt [Math]::Max(1, $HealthFailureThreshold)) {
            if ($consecutiveHealthFailures -eq 1) {
                Write-LiveLine "[local-master] dashboard health probe missed once; confirming before entering recovery."
            }
            continue
        }
        if (-not $restartRecoveryActive) {
            Write-LiveLine "[local-master] restart detected: dashboard health became unavailable while worker process stayed alive."
            Write-LiveLine "[local-master] waiting for recovery: dashboard health and core startup readiness will be rechecked."
        }
        $restartRecoveryActive = $true
        $dashboardReady = $false
        $coreReady = $false
        $coreStartedAt = $null
        $lastProgressAt = -100
        $dashboardTimeoutReported = $false
        $coreTimeoutReported = $false
        $lastCoreDetail = ""
        $consecutiveHealthFailures = 0
        continue
    }
    if ($consecutiveHealthFailures -gt 0) {
        Write-LiveLine "[local-master] dashboard health probe recovered before the recovery threshold."
        $consecutiveHealthFailures = 0
    }

    if (-not $coreReady) {
        $coreElapsed = [int][Math]::Floor(($now - $coreStartedAt).TotalSeconds)
        $readinessStatus = Get-CoreStartupReadiness
        if ($readinessStatus.Ready) {
            $coreReady = $true
            if (-not [string]::IsNullOrWhiteSpace($lastReadinessQueryWarning)) {
                Write-LiveLine "[local-master] startup readiness query recovered."
                $lastReadinessQueryWarning = ""
            }
            if ($restartRecoveryActive) {
                Write-LiveLine "[local-master] core startup readiness recovered after dashboard restart."
                $restartRecoveryActive = $false
            }
            Write-StartupProgress -Phase "dashboard health and core state are ready; browser should open now" -ElapsedSeconds $elapsed -TotalSeconds $MasterReadyTimeoutSeconds -Complete:$true
            if (-not $script:StartupCompleted) {
                Write-LiveLine "[local-master] startup complete. Live server log remains open below."
                $script:StartupCompleted = $true
            }
            $autostartStatus = Test-AutostartTargetsReady
            Update-BackgroundReadiness -CoreStatus $readinessStatus -AutostartStatus $autostartStatus
            $steadyCheckAt = Get-Date
            continue
        }

        if ($readinessStatus.Detail -ne $lastCoreDetail) {
            Write-LiveLine "[local-master] core readiness detail: $($readinessStatus.Detail)"
            $lastCoreDetail = $readinessStatus.Detail
        }

        if ($coreElapsed -ge $MasterReadyTimeoutSeconds -and -not $coreTimeoutReported) {
            $coreTimeoutReported = $true
            Write-LiveLine "[local-master] startup note: core readiness has passed the usual $MasterReadyTimeoutSeconds second window; detail: $($readinessStatus.Detail)"
        }

        if (($coreElapsed -eq 0) -or (($elapsed - $lastProgressAt) -ge 5)) {
            Write-StartupProgress -Phase "checking core startup readiness at $ReadinessUrl" -ElapsedSeconds $coreElapsed -TotalSeconds $MasterReadyTimeoutSeconds
            $lastProgressAt = $elapsed
        }
        continue
    }

    if (($now - $steadyCheckAt).TotalSeconds -ge 5) {
        $steadyCheckAt = $now
        $readinessStatus = Get-CoreStartupReadiness
        if (-not $readinessStatus.Available) {
            if ($readinessStatus.Detail -ne $lastReadinessQueryWarning) {
                Write-LiveLine "[local-master] startup readiness query warning after completion: $($readinessStatus.Detail) (nonblocking; startup progress remains complete)."
                $lastReadinessQueryWarning = $readinessStatus.Detail
            }
            $autostartStatus = Test-AutostartTargetsReady
            Update-BackgroundReadiness -CoreStatus $null -AutostartStatus $autostartStatus
            continue
        }
        if (-not [string]::IsNullOrWhiteSpace($lastReadinessQueryWarning)) {
            Write-LiveLine "[local-master] startup readiness query recovered."
            $lastReadinessQueryWarning = ""
        }
        if (-not $readinessStatus.Ready) {
            Write-LiveLine "[local-master] core readiness lost after startup: $($readinessStatus.Detail)"
            Write-LiveLine "[local-master] waiting for recovery: required core state will be revalidated."
            $coreReady = $false
            $coreStartedAt = Get-Date
            $coreTimeoutReported = $false
            $lastProgressAt = -100
            $lastCoreDetail = ""
            $restartRecoveryActive = $true
            continue
        }
        $autostartStatus = Test-AutostartTargetsReady
        Update-BackgroundReadiness -CoreStatus $readinessStatus -AutostartStatus $autostartStatus
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
$latestUvicorn = Get-LatestUvicornGenerationEvidence -LogPath $script:WorkerLogPath
$latestGeneration = if ($null -ne $latestUvicorn.Generation) { [string] $latestUvicorn.Generation } else { "unknown" }
$latestExitCode = if ($latestUvicorn.ExitLogged) { [string] $latestUvicorn.ExitCode } else { "none" }
$lastHealthText = if ($script:LastHealthOkAt) { $script:LastHealthOkAt.ToString("o") } else { "never" }
$lastAutostartText = if ($script:LastAutostartOkAt) { $script:LastAutostartOkAt.ToString("o") } else { "never" }
Write-LiveLine ("[local-master] worker process ended: worker_pid={0} uvicorn_pid=unknown uvicorn_generation={1} uvicorn_generation_started={2} latest_uvicorn_exit_logged={3} latest_uvicorn_exit_code={4} worker_exit_code={5} runtime_seconds={6} last_health_ok_at={7} last_autostart_ok_at={8} normal_marker_exists={9}" -f $process.Id, $latestGeneration, $latestUvicorn.StartLogged, $latestUvicorn.ExitLogged, $latestExitCode, $process.ExitCode, $runtimeSeconds, $lastHealthText, $lastAutostartText, $normalMarkerExists)
if ($latestUvicorn.ExitLogged) {
    Write-LiveLine ("[local-master] latest uvicorn generation {0} exited with {1}." -f $latestGeneration, $latestExitCode)
}
elseif (-not $normalMarkerExists -and $latestUvicorn.StartLogged) {
    Write-LiveLine ("[local-master] process disappeared before clean Uvicorn exit logging for generation {0}; classify this as external/forced termination unless the worker log shows another cause." -f $latestGeneration)
}
Start-Sleep -Milliseconds 100
exit $process.ExitCode
