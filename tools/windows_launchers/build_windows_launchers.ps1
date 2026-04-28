[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$TemplatePath = Join-Path $ScriptDir "TradingToolsLauncher.cs"
$DistDir = Join-Path $RepoRoot "dist\windows-launchers"

if (-not (Test-Path $TemplatePath)) {
    throw "Missing launcher source template: $TemplatePath"
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

$launcherTargets = @(
    @{ ExeName = "Local Trading Tools.exe"; TargetBat = "run_local_master_control.bat" },
    @{ ExeName = "Trading Journal.exe"; TargetBat = "run_trading_journal_local.bat" }
)

$template = Get-Content -Raw -LiteralPath $TemplatePath
$csc = Get-Command csc.exe -ErrorAction SilentlyContinue

if (-not $csc) {
    try {
        Add-Type -TypeDefinition "public static class LauncherCompilerProbe { public static int Value => 1; }" -Language CSharp -ErrorAction Stop | Out-Null
    }
    catch {
        throw @"
No supported C# compiler found.
Install either:
  - .NET SDK / Visual Studio Build Tools so csc.exe is available, or
  - A PowerShell edition that supports Add-Type -Language CSharp compilation.
Then re-run:
  powershell -ExecutionPolicy Bypass -File tools/windows_launchers/build_windows_launchers.ps1
"@
    }
}

foreach ($entry in $launcherTargets) {
    $generatedSource = $template.Replace("__TARGET_BAT__", $entry.TargetBat)
    $tempSourcePath = Join-Path $env:TEMP ("launcher_{0}_{1}.cs" -f ([IO.Path]::GetFileNameWithoutExtension($entry.ExeName) -replace '\\s+', '_'), [Guid]::NewGuid().ToString('N'))
    $outputPath = Join-Path $DistDir $entry.ExeName

    Set-Content -LiteralPath $tempSourcePath -Value $generatedSource -Encoding UTF8

    try {
        if ($csc) {
            & $csc.Path /nologo /target:exe /optimize+ /out:$outputPath $tempSourcePath
            if ($LASTEXITCODE -ne 0) {
                throw "Compilation failed for $($entry.ExeName)"
            }
        }
        else {
            Add-Type -LiteralPath $tempSourcePath -OutputAssembly $outputPath -OutputType ConsoleApplication -Language CSharp -ErrorAction Stop | Out-Null
        }

        Write-Host "Built: $outputPath -> $($entry.TargetBat)"
    }
    finally {
        Remove-Item -LiteralPath $tempSourcePath -ErrorAction SilentlyContinue
    }
}
