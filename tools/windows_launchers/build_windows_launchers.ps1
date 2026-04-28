[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$TemplatePath = Join-Path $ScriptDir "TradingToolsLauncher.cs"
$DistDir = Join-Path $RepoRoot "dist\windows-launchers"
$LocalMasterBat = Join-Path $RepoRoot "run_local_master_control.bat"
$TradingJournalBat = Join-Path $RepoRoot "run_trading_journal_local.bat"

Write-Host "Building Windows launchers from repo root: $RepoRoot"

$requiredPaths = @($LocalMasterBat, $TradingJournalBat, $TemplatePath)
foreach ($required in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "ERROR: This script must be run from a valid CODEX-master checkout or via build_windows_launchers.bat. Missing: $required"
    }
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

$launcherTargets = @(
    @{ ExeName = "Local Trading Tools.exe"; TargetBat = "run_local_master_control.bat" },
    @{ ExeName = "Trading Journal.exe"; TargetBat = "run_trading_journal_local.bat" }
)

function Get-CscCompilerPath {
    $candidates = New-Object System.Collections.Generic.List[string]

    $commandCsc = Get-Command csc.exe -ErrorAction SilentlyContinue
    if ($commandCsc) {
        $candidates.Add($commandCsc.Path)
    }

    $framework64v4 = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    $frameworkv4 = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
    $candidates.Add($framework64v4)
    $candidates.Add($frameworkv4)

    $wildcardPaths = @(
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\*\csc.exe"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework\*\csc.exe")
    )

    foreach ($pattern in $wildcardPaths) {
        Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue |
            ForEach-Object { $candidates.Add($_.FullName) }
    }

    $existing = $candidates |
        Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
        Select-Object -Unique

    if (-not $existing) {
        return $null
    }

    $ranked = $existing | Sort-Object -Descending -Property @(
        { if ($_ -like "*Framework64*") { 1 } else { 0 } },
        {
            $versionFolder = Split-Path (Split-Path $_ -Parent) -Leaf
            try {
                [version]($versionFolder -replace '^[^0-9]*', '')
            }
            catch {
                [version]"0.0.0.0"
            }
        }
    )

    return $ranked[0]
}

function Test-AddTypeCompiler {
    try {
        Add-Type -TypeDefinition "public static class LauncherCompilerProbe { public static int Value => 1; }" -Language CSharp -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Build-WithCSharp {
    param(
        [Parameter(Mandatory = $true)] [string] $Template,
        [Parameter(Mandatory = $true)] [array] $Targets,
        [Parameter(Mandatory = $true)] [string] $OutDir,
        [Parameter()] [string] $CscPath,
        [Parameter(Mandatory = $true)] [bool] $UseAddType
    )

    foreach ($entry in $Targets) {
        $generatedSource = $Template.Replace("__TARGET_BAT__", $entry.TargetBat)
        $tempSourcePath = Join-Path $env:TEMP ("launcher_{0}_{1}.cs" -f ([IO.Path]::GetFileNameWithoutExtension($entry.ExeName) -replace '\\s+', '_'), [Guid]::NewGuid().ToString('N'))
        $outputPath = Join-Path $OutDir $entry.ExeName

        Set-Content -LiteralPath $tempSourcePath -Value $generatedSource -Encoding UTF8
        try {
            if ($CscPath) {
                & $CscPath /nologo /target:exe /optimize+ /out:$outputPath $tempSourcePath
                if ($LASTEXITCODE -ne 0) {
                    throw "Compilation failed for $($entry.ExeName)"
                }
            }
            elseif ($UseAddType) {
                Add-Type -LiteralPath $tempSourcePath -OutputAssembly $outputPath -OutputType ConsoleApplication -Language CSharp -ErrorAction Stop | Out-Null
            }

            if (-not (Test-Path -LiteralPath $outputPath)) {
                throw "Compilation reported success but output was not created: $outputPath"
            }

            Write-Host "Built: $outputPath -> $($entry.TargetBat)"
        }
        finally {
            Remove-Item -LiteralPath $tempSourcePath -ErrorAction SilentlyContinue
        }
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
if not "%TARGET_EXIT%"=="0" (
    echo ERROR: Target batch exited with code %TARGET_EXIT%.
)
exit /b %TARGET_EXIT%
"@

    Set-Content -LiteralPath $OutputCmd -Value $cmdContent -Encoding ASCII
}

function Build-WithIExpress {
    param(
        [Parameter(Mandatory = $true)] [array] $Targets,
        [Parameter(Mandatory = $true)] [string] $RepoRootPath,
        [Parameter(Mandatory = $true)] [string] $OutDir,
        [Parameter(Mandatory = $true)] [string] $IExpressPath
    )

    $allBuilt = $true

    foreach ($entry in $Targets) {
        $outputPath = Join-Path $OutDir $entry.ExeName
        $tempRoot = Join-Path $env:TEMP ("iexpress_launcher_{0}" -f [Guid]::NewGuid().ToString('N'))
        $stagingDir = Join-Path $tempRoot "staging"
        $launcherCmd = Join-Path $stagingDir "launcher.cmd"
        $sedPath = Join-Path $tempRoot "launcher.sed"
        $stdoutPath = Join-Path $tempRoot "iexpress.stdout.log"
        $stderrPath = Join-Path $tempRoot "iexpress.stderr.log"

        New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

        New-IExpressLauncherScript -RepoRootPath $RepoRootPath -TargetBat $entry.TargetBat -OutputCmd $launcherCmd

        $sourceDirForSed = ('{0}\' -f $stagingDir)
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
TargetName=$outputPath
FriendlyName=$($entry.ExeName)
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

        if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $outputPath)) {
            $allBuilt = $false
            Write-Error "IExpress build failed for $($entry.ExeName)."
            Write-Host "IExpress SED file: $sedPath"
            Write-Host "IExpress staging directory: $stagingDir"
            Write-Host "--- IExpress stdout ---"
            Write-Host $stdout
            Write-Host "--- IExpress stderr ---"
            Write-Host $stderr

            $logCandidates = Get-ChildItem -Path $tempRoot -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'iexpress|\.log$|\.txt$' }
            if ($logCandidates) {
                foreach ($logFile in $logCandidates) {
                    Write-Host "--- IExpress log: $($logFile.FullName) ---"
                    Write-Host (Get-Content -Raw -LiteralPath $logFile.FullName)
                }
            }

            Write-Warning "IExpress debug files preserved at: $tempRoot"
            continue
        }

        Write-Host "Built (IExpress): $outputPath -> $($entry.TargetBat)"
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }

    return $allBuilt
}

function New-ShortcutFallbacks {
    param(
        [Parameter(Mandatory = $true)] [array] $Targets,
        [Parameter(Mandatory = $true)] [string] $RepoRootPath,
        [Parameter(Mandatory = $true)] [string] $OutDir
    )

    $shell = New-Object -ComObject WScript.Shell
    foreach ($entry in $Targets) {
        $shortcutPath = Join-Path $OutDir ([IO.Path]::GetFileNameWithoutExtension($entry.ExeName) + ".lnk")
        $targetBatPath = Join-Path $RepoRootPath $entry.TargetBat
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $env:WINDIR "System32\cmd.exe"
        $shortcut.Arguments = ('/d /s /c ""{0}""' -f $targetBatPath)
        $shortcut.WorkingDirectory = $RepoRootPath
        $shortcut.Save()
        Write-Host "Created fallback shortcut: $shortcutPath"
    }

    Write-Warning "WARNING: Could not create .exe launchers. Created .lnk shortcuts instead."
}

$template = Get-Content -Raw -LiteralPath $TemplatePath
$cscPath = Get-CscCompilerPath
if ($cscPath) {
    Write-Host "Using csc.exe: $cscPath"
    Build-WithCSharp -Template $template -Targets $launcherTargets -OutDir $DistDir -CscPath $cscPath -UseAddType:$false
}
else {
    $canUseAddType = Test-AddTypeCompiler
    if ($canUseAddType) {
        Write-Host "Using Add-Type CSharp compiler path."
        Build-WithCSharp -Template $template -Targets $launcherTargets -OutDir $DistDir -UseAddType:$true
    }
    else {
        $iexpressPath = Join-Path $env:WINDIR "System32\iexpress.exe"
        if (Test-Path -LiteralPath $iexpressPath) {
            Write-Warning "No C# compiler found. Falling back to IExpress launcher generation."
            Write-Warning "These launcher executables contain the current repo path. Rebuild them if the repo folder is moved."
            $iExpressBuiltAll = Build-WithIExpress -Targets $launcherTargets -RepoRootPath $RepoRoot -OutDir $DistDir -IExpressPath $iexpressPath
            if (-not $iExpressBuiltAll) {
                New-ShortcutFallbacks -Targets $launcherTargets -RepoRootPath $RepoRoot -OutDir $DistDir
            }
        }
        else {
            New-ShortcutFallbacks -Targets $launcherTargets -RepoRootPath $RepoRoot -OutDir $DistDir
            throw @"
ERROR: No supported executable builder found.
Install .NET SDK / Visual Studio Build Tools, or use a Windows installation with iexpress.exe available.
"@
        }
    }
}

$requiredExeOutputs = $launcherTargets | ForEach-Object { Join-Path $DistDir $_.ExeName }
$missingExeOutputs = $requiredExeOutputs | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missingExeOutputs) {
    Write-Error "Build did not produce all required .exe launchers. Missing: $($missingExeOutputs -join ', ')"
    exit 1
}

Write-Host "Successfully created launcher executables:"
$requiredExeOutputs | ForEach-Object { Write-Host "  $_" }
exit 0
