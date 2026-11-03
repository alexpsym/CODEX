# Compatibility only. The single implementation is cleanup_pytest_temp.py.
# Normal route: & 'C:\Users\User\miniconda3\python.exe' tools/cleanup_pytest_temp.py --mode Audit
param(
 [string]$RepoRoot = (Get-Location).Path,
 [ValidateSet('Audit','Apply')][string]$Mode = 'Audit',
 [string]$Manifest = '',
 [string]$Result = '',
 [switch]$Reconcile,
 [string[]]$AuditRoot = @(),
 [string[]]$Exclude = @(),
 [string]$Python = 'C:\Users\User\miniconda3\python.exe'
)
$cleanerArgs = @((Join-Path $PSScriptRoot 'cleanup_pytest_temp.py'), '--repo-root', $RepoRoot, '--mode', $Mode)
if ($Manifest) { $cleanerArgs += @('--manifest', $Manifest) }
if ($Result) { $cleanerArgs += @('--result', $Result) }
foreach ($item in $AuditRoot) { $cleanerArgs += @('--audit-root', $item) }
foreach ($item in $Exclude) { $cleanerArgs += @('--exclude', $item) }
if ($Reconcile) { $cleanerArgs += '--reconcile' }
& $Python @cleanerArgs
exit $LASTEXITCODE
