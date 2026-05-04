param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFilePath,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'

$allow = @(
    'RENDER_CALCULATOR_BASE_URL',
    'PUBLIC_WEBHOOK_BASE_URL',
    'RENDER_EXTERNAL_URL',
    'LOCAL_STATE_ONLY',
    'DROPBOX_SYNC_ENABLED',
    'DROPBOX_BACKUP_PATH',
    'DROPBOX_STATE_ROOT'
)

if (-not (Test-Path -LiteralPath $EnvFilePath)) {
    Set-Content -LiteralPath $OutputPath -Value @() -Encoding UTF8
    exit 0
}

$lines = New-Object System.Collections.Generic.List[string]

Get-Content -LiteralPath $EnvFilePath | ForEach-Object {
    $line = [string]$_
    if ([string]::IsNullOrWhiteSpace($line)) { return }

    $trim = $line.Trim()
    if ($trim.StartsWith('#')) { return }

    $idx = $trim.IndexOf('=')
    if ($idx -lt 1) { return }

    $k = $trim.Substring(0, $idx).Trim()
    $v = $trim.Substring($idx + 1).Trim()

    if (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'"))) {
        $v = $v.Substring(1, [Math]::Max(0, $v.Length - 2))
    }

    if ($k -like 'DROPBOX_*' -or $allow -contains $k) {
        $lines.Add(('{0}={1}' -f $k, $v))
    }
}

Set-Content -LiteralPath $OutputPath -Value $lines -Encoding UTF8
