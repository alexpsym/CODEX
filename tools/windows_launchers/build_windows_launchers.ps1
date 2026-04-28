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
        [Parameter()] $CscCommand,
        [Parameter(Mandatory = $true)] [bool] $UseAddType
    )

    foreach ($entry in $Targets) {
        $generatedSource = $Template.Replace("__TARGET_BAT__", $entry.TargetBat)
        $tempSourcePath = Join-Path $env:TEMP ("launcher_{0}_{1}.cs" -f ([IO.Path]::GetFileNameWithoutExtension($entry.ExeName) -replace '\\s+', '_'), [Guid]::NewGuid().ToString('N'))
        $outputPath = Join-Path $OutDir $entry.ExeName

        Set-Content -LiteralPath $tempSourcePath -Value $generatedSource -Encoding UTF8
        try {
            if ($CscCommand) {
                & $CscCommand.Path /nologo /target:exe /optimize+ /out:$outputPath $tempSourcePath
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

    foreach ($entry in $Targets) {
        $outputPath = Join-Path $OutDir $entry.ExeName
        $tempRoot = Join-Path $env:TEMP ("iexpress_launcher_{0}" -f [Guid]::NewGuid().ToString('N'))
        $stagingDir = Join-Path $tempRoot "staging"
        $launcherCmd = Join-Path $stagingDir "launcher.cmd"
        $sedPath = Join-Path $tempRoot "launcher.sed"

        New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

        try {
            New-IExpressLauncherScript -RepoRootPath $RepoRootPath -TargetBat $entry.TargetBat -OutputCmd $launcherCmd

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
AppLaunched=launcher.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=launcher.cmd
UserQuietInstCmd=launcher.cmd
SourceFiles=SourceFiles

[SourceFiles]
SourceFiles0=$stagingDir

[SourceFiles0]
launcher.cmd=
"@

            Set-Content -LiteralPath $sedPath -Value $sedContent -Encoding ASCII

            & $IExpressPath /N /Q /M $sedPath
            if ($LASTEXITCODE -ne 0) {
                throw "IExpress build failed for $($entry.ExeName)"
            }

            if (-not (Test-Path -LiteralPath $outputPath)) {
                throw "IExpress did not produce expected output: $outputPath"
            }

            Write-Host "Built (IExpress): $outputPath -> $($entry.TargetBat)"
        }
        finally {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

$template = Get-Content -Raw -LiteralPath $TemplatePath
$csc = Get-Command csc.exe -ErrorAction SilentlyContinue
$canUseAddType = Test-AddTypeCompiler

if ($csc) {
    Build-WithCSharp -Template $template -Targets $launcherTargets -OutDir $DistDir -CscCommand $csc -UseAddType:$false
    exit 0
}

if ($canUseAddType) {
    Build-WithCSharp -Template $template -Targets $launcherTargets -OutDir $DistDir -UseAddType:$true
    exit 0
}

$iexpressPath = Join-Path $env:WINDIR "System32\iexpress.exe"
if (Test-Path -LiteralPath $iexpressPath) {
    Write-Warning "No C# compiler found. Falling back to IExpress launcher generation."
    Write-Warning "These launcher executables contain the current repo path. Rebuild them if the repo folder is moved."
    Build-WithIExpress -Targets $launcherTargets -RepoRootPath $RepoRoot -OutDir $DistDir -IExpressPath $iexpressPath
    exit 0
}

throw @"
ERROR: No supported executable builder found.
Install .NET SDK / Visual Studio Build Tools, or use a Windows installation with iexpress.exe available.
"@
