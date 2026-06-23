param(
    [string]$RepoRoot = (Resolve-Path ".").Path
)

$ErrorActionPreference = "Continue"
$workspace = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepoRoot).Path)
$workspaceRoot = $workspace.TrimEnd("\") + "\"
$failed = New-Object System.Collections.Generic.List[string]

function Remove-SafeDirectory {
    param([Parameter(Mandatory=$true)][string]$Path)

    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Warning "Skipping path outside repo: $full"
        return
    }
    if (-not (Test-Path -LiteralPath $full -PathType Container)) {
        return
    }
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        try {
            Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction Stop
            Write-Host "Removed $full"
            return
        } catch {
            if ($attempt -lt 2) {
                Start-Sleep -Milliseconds 500
            } else {
                $failed.Add($full) | Out-Null
                Write-Warning "Could not remove locked temp folder: $full"
            }
        }
    }
}

Get-ChildItem -LiteralPath $workspace -Directory -Force |
    Where-Object { $_.Name -like ".pytest_tmp*" -or $_.Name -eq ".pytest_cache" -or $_.Name -eq "__pycache__" } |
    ForEach-Object { Remove-SafeDirectory -Path $_.FullName }

Get-ChildItem -LiteralPath $workspace -Directory -Recurse -Force -Filter "__pycache__" |
    ForEach-Object { Remove-SafeDirectory -Path $_.FullName }

if ($failed.Count -gt 0) {
    Write-Host "Locked temp folders left for manual cleanup:"
    $failed | ForEach-Object { Write-Host $_ }
}

exit 0
