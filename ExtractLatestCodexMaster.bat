@echo off
setlocal
set "__BATFILE=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath $env:__BATFILE | Select-Object -Skip 7 | Out-String | Invoke-Expression"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
# POWERSHELL SCRIPT STARTS BELOW

$ErrorActionPreference = 'Stop'

$repoUrl = 'https://github.com/alexpsym/CODEX.git'
$repoBranch = 'master'
$repoFolderName = 'CODEX-master'

function Write-Section {
    param([string]$Message)
    Write-Host ''
    Write-Host $Message
}

function Get-Win32ProcessesSafe {
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop)
    } catch {
        try {
            return @(Get-WmiObject Win32_Process -ErrorAction Stop)
        } catch {
            Write-Host "WARNING: Unable to inspect running processes: $($_.Exception.Message)"
            return @()
        }
    }
}

function Stop-ProcessTreeById {
    param(
        [Parameter(Mandatory = $true)] [int] $ProcessId,
        [string] $Reason = 'requested shutdown'
    )

    if ($ProcessId -le 0) {
        return
    }

    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if (-not $proc) {
            return
        }

        Write-Host "Stopping PID $ProcessId ($($proc.ProcessName)) - $Reason"
        $taskkill = Join-Path $env:WINDIR 'System32\taskkill.exe'
        if (Test-Path -LiteralPath $taskkill -PathType Leaf) {
            & $taskkill /PID $ProcessId /T /F > $null 2>&1
        } else {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Host "WARNING: Failed to stop PID $ProcessId - $($_.Exception.Message)"
    }
}

function Get-LocalMasterPortPids {
    $foundPids = New-Object System.Collections.Generic.List[int]
    try {
        $netstat = & netstat.exe -ano -p tcp 2>$null
        foreach ($line in $netstat) {
            if ($line -match '^\s*TCP\s+\S+:8000\s+\S+\s+LISTENING\s+(\d+)\s*$') {
                $pidValue = 0
                if ([int]::TryParse($Matches[1], [ref]$pidValue) -and $pidValue -gt 0) {
                    $foundPids.Add($pidValue)
                }
            }
        }
    } catch {}

    return @($foundPids | Select-Object -Unique)
}

function Get-LocalTradingJournalPortPids {
    $foundPids = New-Object System.Collections.Generic.List[int]
    try {
        $netstat = & netstat.exe -ano -p tcp 2>$null
        foreach ($line in $netstat) {
            if ($line -match '^\s*TCP\s+\S+:8010\s+\S+\s+LISTENING\s+(\d+)\s*$') {
                $pidValue = 0
                if ([int]::TryParse($Matches[1], [ref]$pidValue) -and $pidValue -gt 0) {
                    $foundPids.Add($pidValue)
                }
            }
        }
    } catch {}

    return @($foundPids | Select-Object -Unique)
}

function Get-LocalMasterProcessMatches {
    $processes = Get-Win32ProcessesSafe
    $matchedProcesses = New-Object System.Collections.Generic.List[object]

    foreach ($process in $processes) {
        $cmd = [string]$process.CommandLine
        if (-not $cmd) {
            continue
        }

        $isLocalMaster =
            ($cmd -match '(?i)run_local_master_control\.bat') -or
            ($cmd -match '(?i)uvicorn' -and $cmd -match '(?i)render\.master_service:app' -and $cmd -match '(?i)--port(\s+|=)8000')

        if ($isLocalMaster) {
            $matchedProcesses.Add($process)
        }
    }

    $portPids = Get-LocalMasterPortPids
    foreach ($pidValue in $portPids) {
        $alreadyMatched = $false
        foreach ($match in $matchedProcesses) {
            if ([int]$match.ProcessId -eq [int]$pidValue) {
                $alreadyMatched = $true
                break
            }
        }

        if (-not $alreadyMatched) {
            $portProc = $processes | Where-Object { [int]$_.ProcessId -eq [int]$pidValue } | Select-Object -First 1
            if ($portProc) {
                $portName = [string]$portProc.Name
                $portCmd = [string]$portProc.CommandLine
                if ($portName -match '(?i)^(python|python3|py|uvicorn)\.exe$' -or $portCmd -match '(?i)(uvicorn|render\.master_service)') {
                    $matchedProcesses.Add($portProc)
                }
            }
        }
    }

    return @($matchedProcesses | Sort-Object ProcessId -Unique)
}

function Get-LocalTradingJournalProcessMatches {
    $processes = Get-Win32ProcessesSafe
    $matchedProcesses = New-Object System.Collections.Generic.List[object]

    foreach ($process in $processes) {
        $cmd = [string]$process.CommandLine
        if (-not $cmd) {
            continue
        }

        $isLocalTradingJournal =
            ($cmd -match '(?i)run_trading_journal_local\.bat') -or
            ($cmd -match '(?i)Local Trading Journal') -or
            ($cmd -match '(?i)APP_PROFILE=journal') -or
            ($cmd -match '(?i)TRADING_JOURNAL_SOURCE=local') -or
            ($cmd -match '(?i)uvicorn' -and $cmd -match '(?i)render\.master_service:app' -and $cmd -match '(?i)--port(\s+|=)8010')

        if ($isLocalTradingJournal) {
            $matchedProcesses.Add($process)
        }
    }

    $portPids = Get-LocalTradingJournalPortPids
    foreach ($pidValue in $portPids) {
        $alreadyMatched = $false
        foreach ($match in $matchedProcesses) {
            if ([int]$match.ProcessId -eq [int]$pidValue) {
                $alreadyMatched = $true
                break
            }
        }

        if (-not $alreadyMatched) {
            $portProc = $processes | Where-Object { [int]$_.ProcessId -eq [int]$pidValue } | Select-Object -First 1
            if ($portProc) {
                $portName = [string]$portProc.Name
                $portCmd = [string]$portProc.CommandLine
                if ($portName -match '(?i)^(python|python3|py|uvicorn)\.exe$' -or $portCmd -match '(?i)(uvicorn|render\.master_service|run_trading_journal_local)') {
                    $matchedProcesses.Add($portProc)
                }
            }
        }
    }

    return @($matchedProcesses | Sort-Object ProcessId -Unique)
}

function Close-LocalTradingToolsBrowserPages {
    Write-Section 'Closing Microsoft Edge completely before continuing...'
    Write-Host 'This script does not use Chrome/Brave tab automation.'

    $edgeProcesses = @(Get-Process -Name 'msedge' -ErrorAction SilentlyContinue)
    if (-not $edgeProcesses -or $edgeProcesses.Count -eq 0) {
        Write-Host 'No running Microsoft Edge process was detected.'
        return
    }

    $taskkill = Join-Path $env:WINDIR 'System32\taskkill.exe'
    if (-not (Test-Path -LiteralPath $taskkill -PathType Leaf)) {
        Write-Host 'ERROR: taskkill.exe was not found. Refusing to fake a successful Edge shutdown.'
        exit 1
    }

    try {
        & $taskkill /IM msedge.exe /T /F > $null 2>&1
    } catch {
        Write-Host "ERROR: Failed to close Microsoft Edge: $($_.Exception.Message)"
        exit 1
    }

    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $remainingEdge = @(Get-Process -Name 'msedge' -ErrorAction SilentlyContinue)
        if (-not $remainingEdge -or $remainingEdge.Count -eq 0) {
            Write-Host 'Microsoft Edge was closed completely.'
            return
        }
    }

    $stillRunning = @(Get-Process -Name 'msedge' -ErrorAction SilentlyContinue)
    if ($stillRunning -and $stillRunning.Count -gt 0) {
        Write-Host ''
        Write-Host 'ERROR: Microsoft Edge is still running after the shutdown timeout.'
        foreach ($edge in $stillRunning) {
            Write-Host " - PID $($edge.Id): $($edge.ProcessName)"
        }
        Write-Host 'Refusing to continue with a false success state.'
        exit 1
    }
}

function Stop-LocalMasterControlScript {
    Write-Section 'Stopping local master control script before continuing...'

    $healthOk = $false
    try {
        $health = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            $healthOk = $true
        }
    } catch {}

    if ($healthOk) {
        foreach ($scriptName in @('bybit_monitor', 'oanda_monitor', 'fxweekend-clone', 'monitor')) {
            try {
                $encoded = [System.Uri]::EscapeDataString($scriptName)
                Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/scripts/$encoded/stop" -TimeoutSec 5 -ErrorAction Stop | Out-Null
                Write-Host "Requested managed script stop: $scriptName"
            } catch {
                Write-Host "Managed script stop skipped/failed for ${scriptName}: $($_.Exception.Message)"
            }
        }
        Start-Sleep -Seconds 2
    }

    $localMasterMatches = @(Get-LocalMasterProcessMatches)
    if (-not $localMasterMatches -or $localMasterMatches.Count -eq 0) {
        Write-Host 'No running local master control process was detected.'
        return
    }

    foreach ($match in $localMasterMatches) {
        Stop-ProcessTreeById -ProcessId ([int]$match.ProcessId) -Reason 'local master control / uvicorn on port 8000'
    }

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        $remaining = @(Get-LocalMasterProcessMatches)
        if (-not $remaining -or $remaining.Count -eq 0) {
            Write-Host 'Local master control script stopped.'
            return
        }

        foreach ($match in $remaining) {
            Stop-ProcessTreeById -ProcessId ([int]$match.ProcessId) -Reason 'local master control still running during shutdown wait'
        }
    }

    $stillRunning = @(Get-LocalMasterProcessMatches)
    if ($stillRunning -and $stillRunning.Count -gt 0) {
        Write-Host ''
        Write-Host 'ERROR: Local master control is still running after the shutdown timeout.'
        foreach ($match in $stillRunning) {
            Write-Host " - PID $($match.ProcessId): $($match.Name) $($match.CommandLine)"
        }
        exit 1
    }
}

function Stop-LocalTradingJournalScript {
    Write-Section 'Stopping old local trading journal script before continuing...'

    $journalMatches = @(Get-LocalTradingJournalProcessMatches)
    if (-not $journalMatches -or $journalMatches.Count -eq 0) {
        Write-Host 'No running local trading journal process was detected.'
        return
    }

    foreach ($match in $journalMatches) {
        Stop-ProcessTreeById -ProcessId ([int]$match.ProcessId) -Reason 'old local trading journal / uvicorn on port 8010'
    }

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        $remaining = @(Get-LocalTradingJournalProcessMatches)
        if (-not $remaining -or $remaining.Count -eq 0) {
            Write-Host 'Local trading journal script stopped.'
            return
        }

        foreach ($match in $remaining) {
            Stop-ProcessTreeById -ProcessId ([int]$match.ProcessId) -Reason 'local trading journal still running during shutdown wait'
        }
    }

    $stillRunning = @(Get-LocalTradingJournalProcessMatches)
    if ($stillRunning -and $stillRunning.Count -gt 0) {
        Write-Host ''
        Write-Host 'ERROR: Local trading journal is still running after the shutdown timeout.'
        foreach ($match in $stillRunning) {
            Write-Host " - PID $($match.ProcessId): $($match.Name) $($match.CommandLine)"
        }
        exit 1
    }
}

function Get-GitExecutable {
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if (-not $git) {
        $git = Get-Command git -ErrorAction SilentlyContinue
    }
    if (-not $git) {
        Write-Host ''
        Write-Host 'ERROR: Git was not found on PATH.'
        Write-Host 'Install Git for Windows, then run this file again:'
        Write-Host 'https://git-scm.com/download/win'
        exit 1
    }
    return $git.Source
}

function Invoke-GitCommand {
    param(
        [Parameter(Mandatory = $true)] [string] $GitExe,
        [Parameter(Mandatory = $true)] [string[]] $Arguments,
        [Parameter(Mandatory = $true)] [string] $WorkingDirectory,
        [switch] $AllowFailure
    )

    Push-Location -LiteralPath $WorkingDirectory
    try {
        Write-Host ''
        Write-Host ("git " + ($Arguments -join ' '))
        & $GitExe @Arguments
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "git $($Arguments -join ' ') failed with exit code $exitCode"
    }

    return $exitCode
}

function Invoke-GitText {
    param(
        [Parameter(Mandatory = $true)] [string] $GitExe,
        [Parameter(Mandatory = $true)] [string[]] $Arguments,
        [Parameter(Mandatory = $true)] [string] $WorkingDirectory,
        [switch] $AllowFailure
    )

    Write-Host ''
    Write-Host ("git " + ($Arguments -join ' '))

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $GitExe
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $psi.Arguments = ConvertTo-NativeArgumentString -Arguments $Arguments

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $proc.StandardInput.Close()
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    $timeoutSeconds = 120
    if (-not $proc.WaitForExit($timeoutSeconds * 1000)) {
        Stop-ProcessTreeById -ProcessId $proc.Id -Reason "git command timeout after $timeoutSeconds seconds"
        throw "git $($Arguments -join ' ') timed out after $timeoutSeconds seconds."
    }
    [void]$proc.WaitForExit()
    [void][System.Threading.Tasks.Task]::WaitAll(@($stdoutTask, $stderrTask), 10000)

    $stdout = $stdoutTask.Result.Trim()
    $stderr = $stderrTask.Result.Trim()
    $exitCode = $proc.ExitCode
    if ($stdout) { Write-Host $stdout }
    if ($stderr -and $exitCode -eq 0) {
        Write-Host ("WARNING: git " + ($Arguments -join ' ') + " stderr: " + $stderr)
    }
    if ($stderr -and $exitCode -ne 0) {
        Write-Host $stderr
    }

    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "git $($Arguments -join ' ') failed with exit code $exitCode. stdout: $stdout stderr: $stderr"
    }

    if ($exitCode -ne 0 -and $AllowFailure) {
        return ("stdout: $stdout`nstderr: $stderr").Trim()
    }

    return $stdout
}

function Write-GitDiagnosticFile {
    param(
        [Parameter(Mandatory = $true)] [string] $GitExe,
        [Parameter(Mandatory = $true)] [string[]] $Arguments,
        [Parameter(Mandatory = $true)] [string] $WorkingDirectory,
        [Parameter(Mandatory = $true)] [string] $DestinationPath
    )

    try {
        $diagnosticText = Invoke-GitText -GitExe $GitExe -Arguments $Arguments -WorkingDirectory $WorkingDirectory -AllowFailure
        Set-Content -LiteralPath $DestinationPath -Value $diagnosticText -Encoding UTF8 -ErrorAction Stop
    } catch {
        $errorPath = "$DestinationPath.error.txt"
        $errorText = "Failed diagnostic command: git $($Arguments -join ' ')`nError: $($_.Exception.Message)"
        try {
            Set-Content -LiteralPath $errorPath -Value $errorText -Encoding UTF8 -ErrorAction Stop
        } catch {
            Write-Host "WARNING: Could not write backup diagnostic $DestinationPath; continuing because full backup already exists."
            return
        }
        Write-Host "WARNING: Could not write backup diagnostic $DestinationPath; continuing because full backup already exists."
    }
}

function ConvertTo-NativeArgumentString {
    param(
        [Parameter(Mandatory = $true)] [string[]] $Arguments
    )

    $quoted = foreach ($arg in $Arguments) {
        if ($null -eq $arg) { '""'; continue }
        if ($arg -eq '') { '""'; continue }

        $needsQuotes = $arg -match '[\s"]'
        if (-not $needsQuotes) {
            $arg
            continue
        }

        $escaped = $arg -replace '(\\*)"', '$1$1\"'
        $escaped = $escaped -replace '(\\+)$', '$1$1'
        '"' + $escaped + '"'
    }

    return ($quoted -join ' ')
}

function Invoke-GitCommandNoInput {
    param(
        [Parameter(Mandatory = $true)] [string] $GitExe,
        [Parameter(Mandatory = $true)] [string[]] $Arguments,
        [Parameter(Mandatory = $true)] [string] $WorkingDirectory,
        [int] $TimeoutSeconds = 120,
        [switch] $AllowFailure
    )

    Write-Host ''
    Write-Host ("git " + ($Arguments -join ' '))

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $GitExe
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $psi.Arguments = ConvertTo-NativeArgumentString -Arguments $Arguments

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $proc.StandardInput.Close()
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-ProcessTreeById -ProcessId $proc.Id -Reason "git command timeout after $TimeoutSeconds seconds"
        throw "git $($Arguments -join ' ') timed out after $TimeoutSeconds seconds."
    }
    [void]$proc.WaitForExit()
    [void][System.Threading.Tasks.Task]::WaitAll(@($stdoutTask, $stderrTask), 10000)
    $stdout = $stdoutTask.Result.Trim()
    $stderr = $stderrTask.Result.Trim()
    if ($stdout) { Write-Host $stdout }
    if ($stderr) { Write-Host $stderr }

    if ($proc.ExitCode -ne 0 -and -not $AllowFailure) {
        throw "git $($Arguments -join ' ') failed with exit code $($proc.ExitCode). stdout: $stdout stderr: $stderr"
    }

    return @{
        ExitCode = $proc.ExitCode
        StdOut = $stdout
        StdErr = $stderr
    }
}

function Stop-CodexRepoProcessMatches {
    param(
        [Parameter(Mandatory = $true)] [string] $RepoDir
    )

    $normalizedRepo = [IO.Path]::GetFullPath($RepoDir).TrimEnd('\').ToLowerInvariant()
    $currentPid = $PID
    $parentPid = 0
    try { $parentPid = [int](Get-CimInstance Win32_Process -Filter "ProcessId=$currentPid" | Select-Object -ExpandProperty ParentProcessId) } catch {}
    $safeCurrentBat = $env:__BATFILE

    $candidates = New-Object System.Collections.Generic.List[object]
    foreach ($process in Get-Win32ProcessesSafe) {
        $pidValue = [int]$process.ProcessId
        if ($pidValue -eq $currentPid -or ($parentPid -gt 0 -and $pidValue -eq $parentPid)) { continue }
        $name = [string]$process.Name
        $cmd = [string]$process.CommandLine
        if (-not $cmd) { continue }
        if ($safeCurrentBat -and $cmd -match [Regex]::Escape($safeCurrentBat)) { continue }

        $cmdLower = $cmd.ToLowerInvariant()
        $nameMatch = $name -match '(?i)^(python|python3|py|uvicorn|cmd|powershell|pwsh)(\.exe)?$'
        $repoRef = $cmdLower.Contains($normalizedRepo) -or $cmdLower.Contains('codex-master')
        $knownLocalRef = ($cmdLower -match 'render\.master_service') -or ($cmdLower -match 'run_local_master_control') -or ($cmdLower -match 'run_trading_journal_local')
        if ($nameMatch -and ($repoRef -or $knownLocalRef)) {
            $candidates.Add($process)
        }
    }

    if ($candidates.Count -eq 0) {
        Write-Host 'No repo-specific locking processes were detected.'
        return
    }

    Write-Host "Stopping repo-specific processes that may lock files in: $RepoDir"
    foreach ($match in @($candidates | Sort-Object ProcessId -Unique)) {
        Write-Host " - PID $($match.ProcessId): $($match.Name) $($match.CommandLine)"
        Stop-ProcessTreeById -ProcessId ([int]$match.ProcessId) -Reason 'repo-specific cleanup lock'
    }

    Start-Sleep -Seconds 2
}

function Test-GitStatusOnlyAllowedLocalData {
    param(
        [string]$StatusText,
        [string[]]$AllowedRootGeneratedFiles = @()
    )
    $result = @{ IsOnlyAllowed = $true; DisallowedLines = @() }
    if ([string]::IsNullOrWhiteSpace($StatusText)) { return $result }
    $lines = $StatusText -split "(`r`n|`n|`r)" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    foreach ($lineRaw in $lines) {
        if ($lineRaw.Length -lt 4) { $result.IsOnlyAllowed = $false; $result.DisallowedLines += $lineRaw; continue }
        $xy = $lineRaw.Substring(0,2)
        $pathRaw = $lineRaw.Substring(3)
        if ([string]::IsNullOrWhiteSpace($pathRaw)) { $result.IsOnlyAllowed = $false; $result.DisallowedLines += $lineRaw; continue }
        if (($xy -ne '??') -and ($xy -ne ' M')) { $result.IsOnlyAllowed = $false; $result.DisallowedLines += $lineRaw; continue }
        $path = $pathRaw.Trim()
        if ($path.StartsWith('"') -and $path.EndsWith('"') -and $path.Length -ge 2) { $path = $path.Substring(1, $path.Length - 2) }
        $p = $path.Replace('\','/')
        if ([string]::IsNullOrWhiteSpace($p)) { $result.IsOnlyAllowed = $false; $result.DisallowedLines += $lineRaw; continue }
        $isAllowedGeneratedRootFile = ($p -notmatch '/') -and ($AllowedRootGeneratedFiles -contains $p)
        $allowed =
            ($p -eq '.env') -or
            ($p -eq 'env.env') -or
            ($p -eq 'watchlist.json') -or
            ($p -eq 'state_manifest.json') -or
            ($p -eq 'stateManifest.json') -or
            ($p -eq 'state_backup.json') -or
            ($p -eq 'journal') -or ($p -eq 'journal/') -or ($p -like 'journal/*') -or
            ($p -like 'bybit_monitor/*.json') -or
            ($p -like 'oanda_monitor/*.json') -or
            ($p -eq 'render/data') -or ($p -eq 'render/data/') -or ($p -like 'render/data/*') -or
            ($p -eq 'render/uploads') -or ($p -eq 'render/uploads/') -or ($p -like 'render/uploads/*') -or
            $isAllowedGeneratedRootFile
        if (($p -like '*__pycache__*') -or -not $allowed) {
            $result.IsOnlyAllowed = $false
            $result.DisallowedLines += $lineRaw
        }
    }
    return $result
}

function Test-GitCleanFailureIsOnlyPythonCachePermissionDenied {
    param([string]$OutputText, [string]$StatusText, [bool]$HeadsSynced)
    if (-not $HeadsSynced) { return $false }
    $statusCheck = Test-GitStatusOnlyAllowedLocalData -StatusText $StatusText
    if (-not $statusCheck.IsOnlyAllowed) { return $false }
    $text = [string]$OutputText
    if ([string]::IsNullOrWhiteSpace($text)) { return $false }
    $lines = $text -split "(`r`n|`n|`r)" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $warnCount = 0
    foreach ($ln in $lines) {
        $t = $ln.Trim()
        if ($t -match '(?i)warning:\s*failed to remove .+__pycache__.+(permission denied|access is denied)') { $warnCount++; continue }
        if ($t -match '(?i)^git clean ') { continue }
        return $false
    }
    return ($warnCount -gt 0)
}

function Remove-PythonCacheDirsBestEffort {
    param([string]$RepoDir)
    $cacheDirs = @(Get-ChildItem -LiteralPath $RepoDir -Directory -Recurse -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq '__pycache__' })
    foreach ($dir in $cacheDirs) {
        try { Remove-Item -LiteralPath $dir.FullName -Recurse -Force -ErrorAction Stop } catch { Write-Host "WARNING: Could not remove Python cache folder: $($dir.FullName) - $($_.Exception.Message)" }
    }
}

function Copy-DirectoryContentsSafe {
    param(
        [Parameter(Mandatory = $true)] [string] $Source,
        [Parameter(Mandatory = $true)] [string] $Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        return
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force -ErrorAction Stop
    }
}

function Copy-FolderTreeWithRoboCopyChecked {
    param(
        [Parameter(Mandatory = $true)] [string] $Source,
        [Parameter(Mandatory = $true)] [string] $Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Backup source folder does not exist: $Source"
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Write-Host "Creating full backup copy from '$Source' to '$Destination'..."
    & robocopy.exe $Source $Destination /E /COPY:DAT /DCOPY:DAT /R:2 /W:2 /NFL /NDL /NP
    $copyExit = $LASTEXITCODE
    if ($copyExit -gt 7) {
        throw "robocopy failed with exit code $copyExit while creating backup."
    }
}

function Preserve-LocalFilesFromBackup {
    param(
        [Parameter(Mandatory = $true)] [string] $BackupDir,
        [Parameter(Mandatory = $true)] [string] $NewRepoDir
    )

    Write-Section 'Preserving local files from backup...'

    $backupJournal = Join-Path $BackupDir 'journal'
    $newJournal = Join-Path $NewRepoDir 'journal'
    if (Test-Path -LiteralPath $backupJournal -PathType Container) {
        Copy-DirectoryContentsSafe -Source $backupJournal -Destination $newJournal
        Write-Host "Preserved journal folder: $newJournal"
    } else {
        Write-Host 'No previous journal folder was found to preserve.'
    }

    foreach ($fileName in @('env.env', '.env')) {
        $oldFile = Join-Path $BackupDir $fileName
        $newFile = Join-Path $NewRepoDir $fileName
        if (Test-Path -LiteralPath $oldFile -PathType Leaf) {
            Copy-Item -LiteralPath $oldFile -Destination $newFile -Force -ErrorAction Stop
            Write-Host "Preserved $fileName"
        }
    }

    foreach ($stateFile in @('watchlist.json', 'state_manifest.json', 'stateManifest.json', 'state_backup.json')) {
        $oldStateFile = Join-Path $BackupDir $stateFile
        $newStateFile = Join-Path $NewRepoDir $stateFile
        if (Test-Path -LiteralPath $oldStateFile -PathType Leaf) {
            Copy-Item -LiteralPath $oldStateFile -Destination $newStateFile -Force -ErrorAction Stop
            Write-Host "Preserved $stateFile"
        }
    }

    foreach ($monitorDir in @('bybit_monitor', 'oanda_monitor')) {
        $backupMonitorDir = Join-Path $BackupDir $monitorDir
        $newMonitorDir = Join-Path $NewRepoDir $monitorDir
        if (Test-Path -LiteralPath $backupMonitorDir -PathType Container) {
            New-Item -ItemType Directory -Force -Path $newMonitorDir | Out-Null
            $jsonFiles = @(Get-ChildItem -LiteralPath $backupMonitorDir -Filter '*.json' -File -ErrorAction SilentlyContinue)
            foreach ($json in $jsonFiles) {
                $destFile = Join-Path $newMonitorDir $json.Name
                Copy-Item -LiteralPath $json.FullName -Destination $destFile -Force -ErrorAction Stop
                Write-Host "Preserved ${monitorDir}/$($json.Name)"
            }
        }
    }

    foreach ($dirName in @('render\data', 'render\uploads')) {
        $backupDataDir = Join-Path $BackupDir $dirName
        $newDataDir = Join-Path $NewRepoDir $dirName
        if (Test-Path -LiteralPath $backupDataDir -PathType Container) {
            Copy-DirectoryContentsSafe -Source $backupDataDir -Destination $newDataDir
            Write-Host "Preserved folder: $newDataDir"
        }
    }
}

function Remove-OldCodexZipsFromDownloads {
    $downloadCandidates = @()
    if ($env:USERPROFILE) {
        $downloadCandidates += (Join-Path $env:USERPROFILE 'Downloads')
    }
    $downloadCandidates += 'C:\Users\User\Downloads'
    $downloadDirs = $downloadCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) } | Select-Object -Unique

    foreach ($dir in $downloadDirs) {
        try {
            $zips = @(Get-ChildItem -LiteralPath $dir -File -Filter '*.zip' -ErrorAction Stop | Where-Object { $_.BaseName -match '(?i)codex.*master|CODEX-master' })
            if ($zips.Count -gt 0) {
                $zips | Remove-Item -Force -ErrorAction Stop
                Write-Host "Deleted $($zips.Count) old CODEX-master zip file(s) from: $dir"
            }
        } catch {
            Write-Host "WARNING: Could not clean old CODEX-master zip files from ${dir}: $($_.Exception.Message)"
        }
    }
}

function Ensure-CodexGitRepo {
    param(
        [Parameter(Mandatory = $true)] [string] $GitExe,
        [Parameter(Mandatory = $true)] [string] $DestinationRoot,
        [Parameter(Mandatory = $true)] [string] $RepoDir,
        [Parameter(Mandatory = $true)] [string] $RepoUrl,
        [Parameter(Mandatory = $true)] [string] $Branch
    )

    if (Test-Path -LiteralPath $RepoDir -PathType Leaf) {
        throw "Cannot create CODEX-master because a file exists at: $RepoDir"
    }

    $gitDir = Join-Path $RepoDir '.git'
    if (Test-Path -LiteralPath $gitDir -PathType Container) {
        Write-Section 'Existing CODEX-master is a Git checkout. Inspecting and syncing it safely...'
        Write-Host $RepoDir

        $originUrl = Invoke-GitText -GitExe $GitExe -Arguments @('remote', 'get-url', 'origin') -WorkingDirectory $RepoDir -AllowFailure
        if (-not $originUrl) {
            Invoke-GitCommand -GitExe $GitExe -Arguments @('remote', 'add', 'origin', $RepoUrl) -WorkingDirectory $RepoDir | Out-Null
            Write-Host "Added origin remote: $RepoUrl"
        } elseif ($originUrl -ne $RepoUrl) {
            Write-Host "Updating origin remote from '$originUrl' to '$RepoUrl'"
            Invoke-GitCommand -GitExe $GitExe -Arguments @('remote', 'set-url', 'origin', $RepoUrl) -WorkingDirectory $RepoDir | Out-Null
        }

        Invoke-GitCommand -GitExe $GitExe -Arguments @('fetch', 'origin', $Branch) -WorkingDirectory $RepoDir | Out-Null
        Invoke-GitCommand -GitExe $GitExe -Arguments @('rev-parse', '--verify', "origin/$Branch") -WorkingDirectory $RepoDir | Out-Null

        $currentBranch = Invoke-GitText -GitExe $GitExe -Arguments @('rev-parse', '--abbrev-ref', 'HEAD') -WorkingDirectory $RepoDir
        $statusPorcelain = Invoke-GitText -GitExe $GitExe -Arguments @('status', '--porcelain') -WorkingDirectory $RepoDir -AllowFailure
        $aheadBehind = Invoke-GitText -GitExe $GitExe -Arguments @('rev-list', '--left-right', '--count', "HEAD...origin/$Branch") -WorkingDirectory $RepoDir
        $headBefore = Invoke-GitText -GitExe $GitExe -Arguments @('rev-parse', 'HEAD') -WorkingDirectory $RepoDir
        $originHead = Invoke-GitText -GitExe $GitExe -Arguments @('rev-parse', "origin/$Branch") -WorkingDirectory $RepoDir
        $counts = $aheadBehind -split '\s+'
        if ($counts.Count -lt 2) { throw "Unable to parse ahead/behind counts from: $aheadBehind" }
        $aheadCount = [int]$counts[0]
        $behindCount = [int]$counts[1]
        $statusClassification = Test-GitStatusOnlyAllowedLocalData -StatusText $statusPorcelain
        $isDirty = -not [string]::IsNullOrWhiteSpace($statusPorcelain)
        $needsBackupRecovery = $isDirty -or ($aheadCount -gt 0)

        if ($aheadCount -eq 0 -and $behindCount -eq 0 -and $headBefore -eq $originHead -and $statusClassification.IsOnlyAllowed) {
            Write-Host "Repo is already synced to origin/$Branch; only preserved local runtime data is present."
            return
        }

        if ((-not $needsBackupRecovery) -or ($behindCount -gt 0 -and $aheadCount -eq 0 -and $statusClassification.IsOnlyAllowed)) {
            Write-Host "Repo is clean and not ahead (behind=$behindCount). Attempting fast-forward sync..."
            Invoke-GitCommand -GitExe $GitExe -Arguments @('checkout', $Branch) -WorkingDirectory $RepoDir | Out-Null
            Invoke-GitCommand -GitExe $GitExe -Arguments @('merge', '--ff-only', "origin/$Branch") -WorkingDirectory $RepoDir | Out-Null
            $headAfterFastForward = Invoke-GitText -GitExe $GitExe -Arguments @('rev-parse', 'HEAD') -WorkingDirectory $RepoDir
            $originAfterFastForward = Invoke-GitText -GitExe $GitExe -Arguments @('rev-parse', "origin/$Branch") -WorkingDirectory $RepoDir
            if ($headAfterFastForward -ne $originAfterFastForward) {
                throw "Fast-forward merge completed but HEAD is not equal to origin/$Branch."
            }
            Write-Host 'Git checkout updated successfully by fast-forward merge.'
            return
        }

        Write-Host "Local Git state requires backup recovery (dirty=$isDirty, ahead=$aheadCount, behind=$behindCount)."
        $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backupDir = Join-Path $DestinationRoot "CODEX-master-git-backup-$timestamp"
        Copy-FolderTreeWithRoboCopyChecked -Source $RepoDir -Destination $backupDir

        Write-GitDiagnosticFile -GitExe $GitExe -Arguments @('status', '--short', '--branch') -WorkingDirectory $RepoDir -DestinationPath (Join-Path $backupDir 'git-status-before-reset.txt')
        Write-GitDiagnosticFile -GitExe $GitExe -Arguments @('log', '--oneline', "origin/$Branch..HEAD") -WorkingDirectory $RepoDir -DestinationPath (Join-Path $backupDir 'git-log-local-ahead.txt')
        Write-GitDiagnosticFile -GitExe $GitExe -Arguments @('diff', '--binary') -WorkingDirectory $RepoDir -DestinationPath (Join-Path $backupDir 'local-changes.patch')
        Write-GitDiagnosticFile -GitExe $GitExe -Arguments @('diff', '--cached', '--binary') -WorkingDirectory $RepoDir -DestinationPath (Join-Path $backupDir 'local-staged-changes.patch')

        Write-Host "Local Git state was not fast-forwardable. A full backup was created at: $backupDir"
        Stop-CodexRepoProcessMatches -RepoDir $RepoDir
        Write-Host "Recovery command: git checkout -B $Branch origin/$Branch"
        Invoke-GitCommand -GitExe $GitExe -Arguments @('checkout', '-B', $Branch, "origin/$Branch") -WorkingDirectory $RepoDir | Out-Null
        Write-Host "Recovery command: git reset --hard origin/$Branch"
        Invoke-GitCommand -GitExe $GitExe -Arguments @('reset', '--hard', "origin/$Branch") -WorkingDirectory $RepoDir | Out-Null
        Remove-PythonCacheDirsBestEffort -RepoDir $RepoDir
        Write-Host "Recovery command: git clean -ffdn (preview)"
        Invoke-GitCommand -GitExe $GitExe -Arguments @('clean', '-ffdn', '-e', 'journal/', '-e', '.env', '-e', 'env.env', '-e', 'watchlist.json', '-e', 'state_manifest.json', '-e', 'stateManifest.json', '-e', 'state_backup.json', '-e', 'bybit_monitor/*.json', '-e', 'oanda_monitor/*.json', '-e', 'render/data/', '-e', 'render/uploads/') -WorkingDirectory $RepoDir -AllowFailure | Out-Null

        $cleaned = $false
        $lastCleanFailure = ''
        for ($attempt = 1; $attempt -le 2; $attempt++) {
            try {
                Write-Host "Recovery command: git clean -ffd -q"
                Invoke-GitCommandNoInput -GitExe $GitExe -Arguments @('clean', '-ffd', '-q', '-e', 'journal/', '-e', '.env', '-e', 'env.env', '-e', 'watchlist.json', '-e', 'state_manifest.json', '-e', 'stateManifest.json', '-e', 'state_backup.json', '-e', 'bybit_monitor/*.json', '-e', 'oanda_monitor/*.json', '-e', 'render/data/', '-e', 'render/uploads/') -WorkingDirectory $RepoDir -TimeoutSeconds 120 | Out-Null
                $cleaned = $true
                break
            } catch {
                $lastCleanFailure = $_.Exception.Message
                Write-Host "WARNING: git clean attempt $attempt failed: $lastCleanFailure"
                $statusNow = Invoke-GitText -GitExe $GitExe -Arguments @('status', '--short') -WorkingDirectory $RepoDir -AllowFailure
                $headNow = Invoke-GitText -GitExe $GitExe -Arguments @('rev-parse', 'HEAD') -WorkingDirectory $RepoDir -AllowFailure
                $originNow = Invoke-GitText -GitExe $GitExe -Arguments @('rev-parse', "origin/$Branch") -WorkingDirectory $RepoDir -AllowFailure
                $headsSynced = ($headNow -and $originNow -and $headNow -eq $originNow)
                if (Test-GitCleanFailureIsOnlyPythonCachePermissionDenied -OutputText $lastCleanFailure -StatusText $statusNow -HeadsSynced $headsSynced) {
                    Write-Host 'WARNING: git clean could not remove locked Python cache folders, but no unsafe Git state remains. Continuing.'
                    $cleaned = $true
                    break
                }
                if ($attempt -lt 2) {
                    Stop-CodexRepoProcessMatches -RepoDir $RepoDir
                    Remove-PythonCacheDirsBestEffort -RepoDir $RepoDir
                }
            }
        }
        if (-not $cleaned) {
            $remainingStatus = Invoke-GitText -GitExe $GitExe -Arguments @('status', '--short') -WorkingDirectory $RepoDir -AllowFailure
            throw "ERROR: Non-interactive git cleanup failed after retries. Backup already exists at: $backupDir`nFailure: $lastCleanFailure`nRemaining git status:`n$remainingStatus"
        }
        Preserve-LocalFilesFromBackup -BackupDir $backupDir -NewRepoDir $RepoDir

        $headAfterRecovery = Invoke-GitText -GitExe $GitExe -Arguments @('rev-parse', 'HEAD') -WorkingDirectory $RepoDir
        $originAfterRecovery = Invoke-GitText -GitExe $GitExe -Arguments @('rev-parse', "origin/$Branch") -WorkingDirectory $RepoDir
        if ($headAfterRecovery -ne $originAfterRecovery) {
            throw "Git checkout is not synced to origin/$Branch after recovery."
        }
        Write-Host "Recovered checkout to origin/$Branch successfully."
        return
    }

    $backupDir = $null
    if (Test-Path -LiteralPath $RepoDir -PathType Container) {
        Write-Section 'Existing CODEX-master folder is not a Git checkout. Moving it aside before clone...'
        $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backupDir = Join-Path $DestinationRoot "CODEX-master-zip-backup-$timestamp"
        Move-Item -LiteralPath $RepoDir -Destination $backupDir -ErrorAction Stop
        Write-Host "Moved old ZIP-style folder to: $backupDir"
    }

    Write-Section 'Cloning CODEX from GitHub so GitHub sync can work...'
    Write-Host "Repo:   $RepoUrl"
    Write-Host "Branch: $Branch"
    Write-Host "Target: $RepoDir"
    Invoke-GitCommand -GitExe $GitExe -Arguments @('clone', '--branch', $Branch, '--single-branch', $RepoUrl, $RepoDir) -WorkingDirectory $DestinationRoot | Out-Null

    if (-not (Test-Path -LiteralPath (Join-Path $RepoDir '.git') -PathType Container)) {
        throw "Git clone completed but .git folder was not found at: $RepoDir"
    }

    if ($backupDir) {
        Preserve-LocalFilesFromBackup -BackupDir $backupDir -NewRepoDir $RepoDir
    }
}

Close-LocalTradingToolsBrowserPages
Stop-LocalMasterControlScript
Stop-LocalTradingJournalScript

if ($env:__BATFILE) {
    $scriptDir = Split-Path -Parent $env:__BATFILE
} else {
    $scriptDir = 'C:\Users\User\Documents\GPT'
}

if ((Split-Path -Leaf $scriptDir) -ieq $repoFolderName) {
    $dest = Split-Path -Parent $scriptDir
} else {
    $dest = $scriptDir
}

if (-not (Test-Path -LiteralPath $dest -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
}

Set-Location -LiteralPath $dest

$codexDir = Join-Path $dest $repoFolderName
$gitExe = Get-GitExecutable

Write-Section 'Using Git instead of ZIP download/extraction.'
Write-Host "Destination root: $dest"
Write-Host "CODEX folder:     $codexDir"
Write-Host "Git executable:   $gitExe"

try {
    Ensure-CodexGitRepo -GitExe $gitExe -DestinationRoot $dest -RepoDir $codexDir -RepoUrl $repoUrl -Branch $repoBranch
} catch {
    Write-Host ''
    Write-Host 'ERROR: Git clone/update failed.'
    Write-Host $_.Exception.Message
    Write-Host ''
    Write-Host 'No fake success state was applied. Fix the Git error above and run this file again.'
    exit 1
}

Write-Section 'Verifying Git sync state before launcher build...'
try {
    Invoke-GitCommand -GitExe $gitExe -Arguments @('fetch', 'origin', $repoBranch) -WorkingDirectory $codexDir | Out-Null
    $headNow = Invoke-GitText -GitExe $gitExe -Arguments @('rev-parse', 'HEAD') -WorkingDirectory $codexDir
    $originNow = Invoke-GitText -GitExe $gitExe -Arguments @('rev-parse', "origin/$repoBranch") -WorkingDirectory $codexDir
    Write-Host "HEAD:              $headNow"
    Write-Host "origin/${repoBranch}: $originNow"
    if ($headNow -ne $originNow) {
        throw 'ERROR: Git checkout is not synced to origin/master after recovery.'
    }
    Write-Host 'Git status --short output:'
    Invoke-GitCommand -GitExe $gitExe -Arguments @('status', '--short') -WorkingDirectory $codexDir -AllowFailure | Out-Null
} catch {
    Write-Host ''
    Write-Host $_.Exception.Message
    exit 1
}

$buildLaunchersBat = Join-Path $codexDir 'build_windows_launchers.bat'
$expectedLaunchers = @(
    (Join-Path $codexDir 'Local Trading Tools.exe')
)
$allowedGeneratedRootFiles = @($expectedLaunchers | ForEach-Object { Split-Path -Leaf $_ } | Select-Object -Unique)

if (-not (Test-Path -LiteralPath $buildLaunchersBat -PathType Leaf)) {
    Write-Host ''
    Write-Host 'ERROR: Launcher build file was not found in CODEX-master:'
    Write-Host $buildLaunchersBat
    exit 1
}

Write-Section 'Building Windows launcher executable...'
Write-Host $buildLaunchersBat

try {
    Push-Location -LiteralPath $codexDir
    try {
        & $buildLaunchersBat
        $launcherBuildExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    if ($launcherBuildExit -ne 0) {
        throw "build_windows_launchers.bat failed with exit code $launcherBuildExit"
    }

    $missingLaunchers = $expectedLaunchers | Where-Object {
        -not (Test-Path -LiteralPath $_ -PathType Leaf)
    }

    if ($missingLaunchers -and $missingLaunchers.Count -gt 0) {
        throw "Launcher build returned success, but expected launcher file(s) were missing: $($missingLaunchers -join ', ')"
    }

    Write-Host ''
    Write-Host 'Windows launcher executable created successfully:'
    $expectedLaunchers | ForEach-Object { Write-Host " - $_" }
} catch {
    Write-Host ''
    Write-Host 'ERROR: Git update succeeded, but Windows launcher creation failed.'
    Write-Host $_.Exception.Message
    exit 1
}

try {
    Remove-OldCodexZipsFromDownloads
} catch {}

Write-Section 'Git status summary:'
try {
    $finalStatus = Invoke-GitText -GitExe $gitExe -Arguments @('status', '--short') -WorkingDirectory $codexDir -AllowFailure
    if ($finalStatus) { Write-Host $finalStatus }
    $finalStatusCheck = Test-GitStatusOnlyAllowedLocalData -StatusText $finalStatus -AllowedRootGeneratedFiles $allowedGeneratedRootFiles
    if ($finalStatusCheck.IsOnlyAllowed) {
        Write-Host 'Only allowed local runtime/user data is present in git status.'
    } else {
        throw "ERROR: Disallowed Git status entries remain:`n$($finalStatusCheck.DisallowedLines -join [Environment]::NewLine)"
    }
} catch {
    Write-Host $_.Exception.Message
    exit 1
}

Write-Host ''
Write-Host 'Everything completed successfully.'
Write-Host "CODEX-master is ready as a real Git checkout at: $codexDir"
Write-Host ''
Write-Host 'GitHub journal sync can now work because this folder contains a .git directory.'
Write-Host 'Next step: run Local Trading Tools, then click Sync Journal.'
Write-Host ''
Write-Host 'Launcher executable:'
$expectedLaunchers | ForEach-Object { Write-Host " - $_" }

exit 0
