[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$TemplatePath = Join-Path $ScriptDir "TradingToolsLauncher.cs"
$LocalMasterBat = Join-Path $RepoRoot "run_local_master_control.bat"
$TradingJournalBat = Join-Path $RepoRoot "run_trading_journal_local.bat"

Write-Host "Building Windows launchers from repo root: $RepoRoot"

$requiredPaths = @($LocalMasterBat, $TradingJournalBat, $TemplatePath)
$missingRequired = $requiredPaths | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missingRequired) {
    Write-Error "ERROR: This script must be run from a valid CODEX-master checkout or via build_windows_launchers.bat."
    foreach ($missing in $missingRequired) {
        Write-Error "Missing required path: $missing"
    }
    exit 1
}

$launcherTargets = @(
    @{ ExeName = "Local Trading Tools.exe"; TargetBat = "run_local_master_control.bat"; OutputPath = (Join-Path $RepoRoot "Local Trading Tools.exe") },
    @{ ExeName = "Trading Journal.exe"; TargetBat = "run_trading_journal_local.bat"; OutputPath = (Join-Path $RepoRoot "Trading Journal.exe") }
)

function Get-CscCompilerCandidates {
    $candidates = New-Object System.Collections.Generic.List[string]

    $commandCsc = Get-Command csc.exe -ErrorAction SilentlyContinue
    if ($commandCsc -and $commandCsc.Path) {
        $candidates.Add($commandCsc.Path)
    }

    $framework64v4 = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    $frameworkv4 = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
    $candidates.Add($framework64v4)
    $candidates.Add($frameworkv4)

    $framework64Matches = Get-ChildItem -Path (Join-Path $env:WINDIR "Microsoft.NET\Framework64\*\csc.exe") -File -ErrorAction SilentlyContinue |
        Sort-Object -Property LastWriteTime -Descending
    foreach ($match in $framework64Matches) {
        $candidates.Add($match.FullName)
    }

    $frameworkMatches = Get-ChildItem -Path (Join-Path $env:WINDIR "Microsoft.NET\Framework\*\csc.exe") -File -ErrorAction SilentlyContinue |
        Sort-Object -Property LastWriteTime -Descending
    foreach ($match in $frameworkMatches) {
        $candidates.Add($match.FullName)
    }

    return $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique
}

function Test-AddTypeCompiler {
    try {
        Add-Type -TypeDefinition "public static class LauncherCompilerProbe { public static int Value = 1; }" -Language CSharp -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        Write-Warning "Add-Type probe failed: $($_.Exception.Message)"
        return $false
    }
}

function Build-WithCsc {
    param(
        [Parameter(Mandatory = $true)] [string] $CompilerPath,
        [Parameter(Mandatory = $true)] [string] $Template,
        [Parameter(Mandatory = $true)] [hashtable] $Target
    )

    $safeBaseName = [IO.Path]::GetFileNameWithoutExtension($Target.ExeName) -replace '[^A-Za-z0-9_.-]', '_'
    $tempBuildDir = Join-Path $env:TEMP ("codex_launcher_build_{0}" -f [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempBuildDir -Force | Out-Null

    $generatedSource = $Template.Replace("__TARGET_BAT__", $Target.TargetBat)
    $tempSourcePath = Join-Path $tempBuildDir ($safeBaseName + ".cs")
    $tempOutputPath = Join-Path $tempBuildDir ($safeBaseName + ".exe")
    $compilerLogPath = Join-Path $tempBuildDir ($safeBaseName + ".compiler.log")
    Set-Content -LiteralPath $tempSourcePath -Value $generatedSource -Encoding UTF8

    try {
        $compilerArgs = @(
            "/nologo",
            "/target:exe",
            "/optimize+",
            ("/out:{0}" -f $tempOutputPath),
            $tempSourcePath
        )
        $compilerOutput = & $CompilerPath @compilerArgs 2>&1
        $compilerExitCode = $LASTEXITCODE

        if ($compilerOutput) {
            $compilerOutputText = ($compilerOutput | Out-String)
            Write-Host $compilerOutputText
            Set-Content -LiteralPath $compilerLogPath -Value $compilerOutputText -Encoding UTF8
        }
        else {
            Set-Content -LiteralPath $compilerLogPath -Value "" -Encoding UTF8
        }

        if ($compilerExitCode -ne 0) {
            Write-Warning "Compilation failed for $($Target.ExeName) with compiler exit code $compilerExitCode."
            Write-Warning "Generated C# source preserved at: $tempSourcePath"
            Write-Warning "Compiler debug files preserved at: $tempBuildDir"
            return $false
        }

        if (-not (Test-Path -LiteralPath $tempOutputPath)) {
            Write-Warning "Compilation reported success but output file is missing: $tempOutputPath"
            Write-Warning "Generated C# source preserved at: $tempSourcePath"
            Write-Warning "Compiler debug files preserved at: $tempBuildDir"
            return $false
        }

        Move-Item -LiteralPath $tempOutputPath -Destination $Target.OutputPath -Force
        if (-not (Test-Path -LiteralPath $Target.OutputPath)) {
            Write-Warning "Failed to place compiled launcher at final output path: $($Target.OutputPath)"
            Write-Warning "Generated C# source preserved at: $tempSourcePath"
            Write-Warning "Compiler debug files preserved at: $tempBuildDir"
            return $false
        }

        Remove-Item -LiteralPath $tempBuildDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Built: $($Target.OutputPath) -> $($Target.TargetBat)"
        return $true
    }
    catch {
        Write-Warning "Compilation failed for $($Target.ExeName): $($_.Exception.Message)"
        Write-Warning "Generated C# source preserved at: $tempSourcePath"
        Write-Warning "Compiler debug files preserved at: $tempBuildDir"
        return $false
    }
}

function Build-WithAddType {
    param(
        [Parameter(Mandatory = $true)] [string] $CompilerPath,
        [Parameter(Mandatory = $true)] [string] $Template,
        [Parameter(Mandatory = $true)] [hashtable] $Target
    )

    $safeBaseName = [IO.Path]::GetFileNameWithoutExtension($Target.ExeName) -replace '[^A-Za-z0-9_.-]', '_'
    $tempBuildDir = Join-Path $env:TEMP ("codex_launcher_build_{0}" -f [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempBuildDir -Force | Out-Null

    $generatedSource = $Template.Replace("__TARGET_BAT__", $Target.TargetBat)
    $tempSourcePath = Join-Path $tempBuildDir ($safeBaseName + ".cs")
    $tempOutputPath = Join-Path $tempBuildDir ($safeBaseName + ".exe")
    Set-Content -LiteralPath $tempSourcePath -Value $generatedSource -Encoding UTF8

    try {
        Add-Type -LiteralPath $tempSourcePath -OutputAssembly $tempOutputPath -OutputType ConsoleApplication -Language CSharp -ErrorAction Stop | Out-Null
        if (-not (Test-Path -LiteralPath $tempOutputPath)) {
            Write-Warning "Add-Type reported success but output file is missing: $tempOutputPath"
            Write-Warning "Generated C# source preserved at: $tempSourcePath"
            Write-Warning "Compiler debug files preserved at: $tempBuildDir"
            return $false
        }

        Move-Item -LiteralPath $tempOutputPath -Destination $Target.OutputPath -Force
        if (-not (Test-Path -LiteralPath $Target.OutputPath)) {
            Write-Warning "Failed to place compiled launcher at final output path: $($Target.OutputPath)"
            Write-Warning "Generated C# source preserved at: $tempSourcePath"
            Write-Warning "Compiler debug files preserved at: $tempBuildDir"
            return $false
        }

        Remove-Item -LiteralPath $tempBuildDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Built (Add-Type): $($Target.OutputPath) -> $($Target.TargetBat)"
        return $true
    }
    catch {
        Write-Warning "Add-Type compilation failed for $($Target.ExeName): $($_.Exception.Message)"
        Write-Warning "Generated C# source preserved at: $tempSourcePath"
        Write-Warning "Compiler debug files preserved at: $tempBuildDir"
        return $false
    }
}

function New-IExpressLauncherScript {
    param(
        [Parameter(Mandatory = $true)] [string] $RepoRootPath,
        [Parameter(Mandatory = $true)] [string] $TargetBat,
        [Parameter(Mandatory = $true)] [string] $OutputCmd
    )

    $targetPath = Join-Path $RepoRootPath $TargetBat
    $targetPathEscaped = $targetPath.Replace('"', '""')

    $cmdContent = @"
@echo off
setlocal
set "TARGET_BAT=$targetPathEscaped"
if not exist "%TARGET_BAT%" (
    echo ERROR: Required launcher target not found: %TARGET_BAT%
    echo This launcher is tied to the repo path used at build time. Rebuild launchers after moving the repo.
    exit /b 1
)
call "%TARGET_BAT%" %*
set "TARGET_EXIT=%ERRORLEVEL%"
exit /b %TARGET_EXIT%
"@

    Set-Content -LiteralPath $OutputCmd -Value $cmdContent -Encoding ASCII
}

function Build-WithIExpress {
    param(
        [Parameter(Mandatory = $true)] [string] $IExpressPath,
        [Parameter(Mandatory = $true)] [string] $RepoRootPath,
        [Parameter(Mandatory = $true)] [hashtable] $Target
    )

    $tempRoot = Join-Path $env:TEMP ("iexpress_launcher_{0}" -f [Guid]::NewGuid().ToString('N'))
    $stagingDir = Join-Path $tempRoot "staging"
    $launcherCmd = Join-Path $stagingDir "launcher.cmd"
    $sedPath = Join-Path $tempRoot "launcher.sed"
    $stdoutPath = Join-Path $tempRoot "iexpress.stdout.log"
    $stderrPath = Join-Path $tempRoot "iexpress.stderr.log"

    New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
    New-IExpressLauncherScript -RepoRootPath $RepoRootPath -TargetBat $Target.TargetBat -OutputCmd $launcherCmd

    $sourceDirForSed = ('{0}\\' -f $stagingDir)
    $sedContent = @"
[Version]
Class=IEXPRESS
SEDVersion=3

[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=1
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$($Target.OutputPath)
FriendlyName=$($Target.ExeName)
AppLaunched=cmd /d /c launcher.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=cmd /d /c launcher.cmd
UserQuietInstCmd=cmd /d /c launcher.cmd
SourceFiles=SourceFiles

[SourceFiles]
SourceFiles0=$sourceDirForSed

[SourceFiles0]
launcher.cmd=
"@

    Set-Content -LiteralPath $sedPath -Value $sedContent -Encoding ASCII

    $process = Start-Process -FilePath $IExpressPath -ArgumentList @('/N', '/Q', '/M', $sedPath) -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -Wait -PassThru
    $stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -Raw -LiteralPath $stdoutPath } else { "" }
    $stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -Raw -LiteralPath $stderrPath } else { "" }

    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $Target.OutputPath)) {
        Write-Warning "IExpress build failed for $($Target.ExeName)."
        Write-Host "IExpress staging directory: $stagingDir"
        Write-Host "IExpress SED file: $sedPath"
        Write-Host "--- IExpress stdout ---"
        Write-Host $stdout
        Write-Host "--- IExpress stderr ---"
        Write-Host $stderr
        Write-Warning "IExpress debug files preserved at: $tempRoot"
        return $false
    }

    Write-Host "Built (IExpress): $($Target.OutputPath) -> $($Target.TargetBat)"
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    return $true
}

function New-ShortcutFallbacks {
    param(
        [Parameter(Mandatory = $true)] [array] $Targets,
        [Parameter(Mandatory = $true)] [string] $RepoRootPath
    )

    Write-Warning "WARNING: Creating .lnk files as a last-resort fallback. This does not satisfy .exe launcher requirements."

    $shell = New-Object -ComObject WScript.Shell
    foreach ($entry in $Targets) {
        $shortcutPath = Join-Path $RepoRootPath ([IO.Path]::GetFileNameWithoutExtension($entry.ExeName) + ".lnk")
        $targetBatPath = Join-Path $RepoRootPath $entry.TargetBat
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $env:WINDIR "System32\cmd.exe"
        $shortcut.Arguments = ('/d /s /c ""{0}""' -f $targetBatPath)
        $shortcut.WorkingDirectory = $RepoRootPath
        $shortcut.Save()
        Write-Host "Created fallback shortcut: $shortcutPath"
    }
}

$template = Get-Content -Raw -LiteralPath $TemplatePath
$buildSucceeded = $false

$cscCandidates = Get-CscCompilerCandidates
foreach ($candidate in $cscCandidates) {
    Write-Host "Trying csc.exe: $candidate"
    $allTargetsBuilt = $true
    foreach ($entry in $launcherTargets) {
        if (-not (Build-WithCsc -CompilerPath $candidate -Template $template -Target $entry)) {
            $allTargetsBuilt = $false
            break
        }
    }

    if ($allTargetsBuilt) {
        $buildSucceeded = $true
        break
    }
}

if (-not $buildSucceeded -and (Test-AddTypeCompiler)) {
    Write-Host "Trying Add-Type CSharp compilation fallback."
    $allTargetsBuilt = $true
    foreach ($entry in $launcherTargets) {
        if (-not (Build-WithAddType -Template $template -Target $entry)) {
            $allTargetsBuilt = $false
            break
        }
    }

    if ($allTargetsBuilt) {
        $buildSucceeded = $true
    }
}

if (-not $buildSucceeded) {
    $iexpressPath = Join-Path $env:WINDIR "System32\iexpress.exe"
    if (Test-Path -LiteralPath $iexpressPath) {
        Write-Warning "WARNING: No C# compiler found. Falling back to IExpress launcher generation."
        Write-Warning "WARNING: These launcher executables contain the current repo path. Rebuild them if the repo folder is moved."
        $allTargetsBuilt = $true
        foreach ($entry in $launcherTargets) {
            if (-not (Build-WithIExpress -IExpressPath $iexpressPath -RepoRootPath $RepoRoot -Target $entry)) {
                $allTargetsBuilt = $false
                break
            }
        }

        if ($allTargetsBuilt) {
            $buildSucceeded = $true
        }
    }
}

if (-not $buildSucceeded) {
    try {
        New-ShortcutFallbacks -Targets $launcherTargets -RepoRootPath $RepoRoot
    }
    catch {
        Write-Warning "Shortcut fallback failed: $($_.Exception.Message)"
    }
}

$requiredExeOutputs = $launcherTargets | ForEach-Object { $_.OutputPath }
$missingExeOutputs = $requiredExeOutputs | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missingExeOutputs) {
    Write-Error "Build did not produce all required .exe launchers at repo root. Missing: $($missingExeOutputs -join ', ')"
    exit 1
}

Write-Host "Successfully created launcher executables:"
$requiredExeOutputs | ForEach-Object { Write-Host "  $_" }
exit 0
