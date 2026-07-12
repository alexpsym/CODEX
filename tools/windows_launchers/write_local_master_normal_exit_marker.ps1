[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $MarkerPath,
    [string] $Reason = "batch_exit_request",
    [string] $RequestingAction = "batch_post_uvicorn"
)

$ErrorActionPreference = "Stop"

$recognizedReasons = @(
    "exit_button",
    "launcher_preflight",
    "local_exit",
    "local_shutdown",
    "replacement",
    "batch_exit_request"
)
$recognizedActions = @(
    "local_exit",
    "local_shutdown",
    "legacy_sentinel",
    "launcher_preflight",
    "replacement",
    "batch_post_uvicorn"
)

function Test-RecognizedMarker {
    param([AllowNull()] [string] $Value, [string[]] $KnownValues)

    $text = [string] $Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $false
    }
    return $KnownValues -contains $text.Trim()
}

function Get-ExistingMarkerValidation {
    param([string] $Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{ Valid = $false; Detail = "missing"; Reason = ""; Action = "" }
    }

    $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return [pscustomobject]@{ Valid = $false; Detail = "empty"; Reason = ""; Action = "" }
    }

    try {
        $parsed = $raw | ConvertFrom-Json -ErrorAction Stop
        $reason = ""
        $action = ""
        if ($null -ne $parsed.PSObject.Properties["reason"]) {
            $reason = [string] $parsed.reason
        }
        if ($null -ne $parsed.PSObject.Properties["requesting_action"]) {
            $action = [string] $parsed.requesting_action
        }
        if ((Test-RecognizedMarker -Value $reason -KnownValues $recognizedReasons) -or
            (Test-RecognizedMarker -Value $action -KnownValues $recognizedActions)) {
            return [pscustomobject]@{ Valid = $true; Detail = "json"; Reason = $reason; Action = $action }
        }
        return [pscustomobject]@{ Valid = $false; Detail = "unrecognized_json_reason_or_action"; Reason = $reason; Action = $action }
    }
    catch {
        $legacyTokens = @($recognizedReasons + $recognizedActions) | Where-Object { $_ }
        foreach ($token in $legacyTokens) {
            if ($raw -match [regex]::Escape($token)) {
                return [pscustomobject]@{ Valid = $true; Detail = "legacy_text"; Reason = $token; Action = "" }
            }
        }
        return [pscustomobject]@{ Valid = $false; Detail = "invalid_json"; Reason = ""; Action = "" }
    }
}

function Write-FallbackMarker {
    param([string] $Path)

    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $payload = [ordered]@{
        reason = $Reason
        timestamp = [DateTime]::UtcNow.ToString("o")
        server_pid = ""
        requesting_action = $RequestingAction
    } | ConvertTo-Json -Compress
    $tempPath = "{0}.{1}.{2}.tmp" -f $Path, $PID, ([Threading.Thread]::CurrentThread.ManagedThreadId)
    Set-Content -LiteralPath $tempPath -Value $payload -Encoding UTF8
    Move-Item -LiteralPath $tempPath -Destination $Path -Force
}

$resolvedPath = [IO.Path]::GetFullPath($MarkerPath)
$validation = Get-ExistingMarkerValidation -Path $resolvedPath
if ($validation.Valid) {
    Write-Host ("[local-master] preserving existing normal-exit marker: reason={0} action={1} path={2}" -f $validation.Reason, $validation.Action, $resolvedPath)
    exit 0
}

if ($validation.Detail -ne "missing") {
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddHHmmssfff")
    $diagnosticPath = "{0}.invalid.{1}" -f $resolvedPath, $stamp
    try {
        Copy-Item -LiteralPath $resolvedPath -Destination $diagnosticPath -Force
        Write-Host ("[local-master] invalid existing normal-exit marker ({0}); saved copy: {1}" -f $validation.Detail, $diagnosticPath)
    }
    catch {
        Write-Host ("[local-master] invalid existing normal-exit marker ({0}); could not save copy: {1}" -f $validation.Detail, $_.Exception.Message)
    }
}

Write-FallbackMarker -Path $resolvedPath
Write-Host ("[local-master] wrote fallback normal-exit marker: reason={0} action={1} path={2}" -f $Reason, $RequestingAction, $resolvedPath)
