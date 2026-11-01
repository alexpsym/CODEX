# Compatibility only. The single implementation is cleanup_pytest_temp.py.
# Normal route: & 'C:\Users\User\miniconda3\python.exe' tools/cleanup_pytest_temp.py --mode Audit
param(
 [string]$RepoRoot = (Get-Location).Path,
 [ValidateSet('Audit','Apply')][string]$Mode = 'Audit',
 [string]$Manifest = '',
 [string]$Result = '',
 [string]$Python = 'C:\Users\User\miniconda3\python.exe'
)
$cleanerArgs = @((Join-Path $PSScriptRoot 'cleanup_pytest_temp.py'), '--repo-root', $RepoRoot, '--mode', $Mode)
if ($Manifest) { $cleanerArgs += @('--manifest', $Manifest) }
if ($Result) { $cleanerArgs += @('--result', $Result) }
& $Python @cleanerArgs
exit $LASTEXITCODE
