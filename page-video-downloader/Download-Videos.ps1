# Windows PowerShell 5.1; no profile, permanent PATH or execution-policy changes.
param([switch]$SetupOnly)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$runtime = Join-Path $root '.runtime'
$exitCode = 1
$jobLock = $null
function Fetch([string]$Url, [string]$File) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile ($File + '.part') -TimeoutSec 180
            Move-Item -LiteralPath ($File + '.part') -Destination $File -Force
            return
        } catch {
            if ($attempt -eq 3) { throw 'Dependency download failed after 3 attempts. Check Internet access and try again.' }
            Start-Sleep -Seconds 2
        }
    }
}
function CheckHash([string]$File, [string]$Expected) {
    if ((Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash -ne $Expected.Trim()) {
        throw 'Dependency checksum mismatch. Setup stopped; retry after checking the connection.'
    }
}
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'This portable bundle requires 64-bit Windows 10 or later.' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    try { $jobLock = [IO.File]::Open((Join-Path $runtime 'job.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another instance is running, or this tool folder is not writable. Close the other run and retry.' }
    # Environment variables below affect only this process and its children.
    $env:TEMP = Join-Path $runtime 'temp'
    $env:TMP = $env:TEMP
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $runtime 'browsers'
    $env:npm_config_cache = Join-Path $runtime 'npm-cache'
    $env:DENO_DIR = Join-Path $runtime 'deno-cache'
    New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
    $bin = Join-Path $runtime 'bin'
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    $node = Join-Path $runtime 'node\node.exe'
    if (-not (Test-Path -LiteralPath $node)) {
        Write-Host 'First-run setup: downloading portable Node.js LTS...'
        $releases = Invoke-RestMethod -Uri 'https://nodejs.org/dist/index.json' -TimeoutSec 60
        $version = ($releases | Where-Object { $_.lts -and ($_.files -contains 'win-x64-zip') } | Select-Object -First 1).version
        if ($version -notmatch '^v\d+\.\d+\.\d+$') { throw 'Invalid Node release metadata.' }
        $name = "node-$version-win-x64.zip"
        Fetch "https://nodejs.org/dist/$version/$name" (Join-Path $runtime 'node.zip')
        Fetch "https://nodejs.org/dist/$version/SHASUMS256.txt" (Join-Path $runtime 'node-sha.txt')
        $line = Get-Content (Join-Path $runtime 'node-sha.txt') | Where-Object { $_.EndsWith('  ' + $name) } | Select-Object -First 1
        if (-not $line) { throw 'Node checksum was not found.' }
        CheckHash (Join-Path $runtime 'node.zip') ($line.Split(' ')[0])
        Expand-Archive -LiteralPath (Join-Path $runtime 'node.zip') -DestinationPath (Join-Path $runtime 'node-unpack') -Force
        Move-Item -LiteralPath (Join-Path $runtime "node-unpack\node-$version-win-x64") -Destination (Join-Path $runtime 'node')
    }
    $ytdlp = Join-Path $bin 'yt-dlp.exe'
    if (-not (Test-Path -LiteralPath $ytdlp)) {
        Write-Host 'Installing yt-dlp (includes its EJS solver)...'
        Fetch 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' ($ytdlp + '.new')
        Fetch 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS' (Join-Path $runtime 'ytdlp-sha.txt')
        $line = Get-Content (Join-Path $runtime 'ytdlp-sha.txt') | Where-Object { $_ -match '\s+\*?yt-dlp\.exe$' } | Select-Object -First 1
        if (-not $line) { throw 'yt-dlp checksum was not found.' }
        CheckHash ($ytdlp + '.new') (($line -split '\s+')[0])
        Move-Item -LiteralPath ($ytdlp + '.new') -Destination $ytdlp -Force
    }
    if (-not (Test-Path (Join-Path $bin 'deno.exe'))) {
        Write-Host 'Installing portable Deno for yt-dlp JavaScript challenges...'
        Fetch 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' (Join-Path $runtime 'deno.zip')
        Expand-Archive -LiteralPath (Join-Path $runtime 'deno.zip') -DestinationPath $bin -Force
    }
    if (-not ((Test-Path (Join-Path $bin 'ffmpeg.exe')) -and (Test-Path (Join-Path $bin 'ffprobe.exe')))) {
        Write-Host 'Installing FFmpeg and ffprobe from Gyan (linked by ffmpeg.org)...'
        Fetch 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' (Join-Path $runtime 'ffmpeg.zip')
        Fetch 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256' (Join-Path $runtime 'ffmpeg-sha.txt')
        $hash = ((Get-Content (Join-Path $runtime 'ffmpeg-sha.txt') -Raw).Trim() -split '\s+')[0]
        CheckHash (Join-Path $runtime 'ffmpeg.zip') $hash
        Expand-Archive -LiteralPath (Join-Path $runtime 'ffmpeg.zip') -DestinationPath (Join-Path $runtime 'ffmpeg-unpack') -Force
        foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
            $file = Get-ChildItem (Join-Path $runtime 'ffmpeg-unpack') -Recurse -Filter $name | Select-Object -First 1
            if (-not $file) { throw 'Incomplete FFmpeg archive.' }
            Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $bin $name) -Force
        }
    }
    $cli = Join-Path $runtime 'node\node_modules\npm\bin\npm-cli.js'
    $pw = Join-Path $runtime 'node_modules\playwright\cli.js'
    if (-not (Test-Path -LiteralPath $pw)) {
        Write-Host 'Installing browser discovery engine...'
        $package = Invoke-RestMethod -Uri 'https://registry.npmjs.org/playwright/latest' -TimeoutSec 60
        if ($package.version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid Playwright release metadata.' }
        & $node $cli install --prefix $runtime --no-audit --no-fund --ignore-scripts --registry=https://registry.npmjs.org ("playwright@" + $package.version)
        if ($LASTEXITCODE -ne 0) { throw 'Playwright package installation failed. Retry setup.' }
    }
    if (-not (Test-Path (Join-Path $runtime 'browser-ready'))) {
        & $node $pw install chromium --only-shell
        if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed. Retry setup.' }
        Set-Content -LiteralPath (Join-Path $runtime 'browser-ready') -Value 'ready'
    }
    foreach ($exe in @($ytdlp, (Join-Path $bin 'ffmpeg.exe'), (Join-Path $bin 'ffprobe.exe'), (Join-Path $bin 'deno.exe'))) {
        $versionFlag = '--version'
        if ([IO.Path]::GetFileName($exe) -in @('ffmpeg.exe', 'ffprobe.exe')) { $versionFlag = '-version' }
        $ErrorActionPreference = 'Continue'
        & $exe $versionFlag *> $null
        $dependencyExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($dependencyExit -ne 0) { throw 'A portable dependency could not start. Check antivirus quarantine and Windows compatibility.' }
    }
    if ($SetupOnly) { $exitCode = 0 } else {
        & $node (Join-Path $root 'downloader.cjs')
        $exitCode = $LASTEXITCODE
    }
} catch {
    Write-Host ('Unable to continue: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'No machine settings were changed. Partial setup can be retried by launching again.'
} finally {
    if ($jobLock) { $jobLock.Dispose() }
    if (-not $SetupOnly) {
        Write-Host 'Press Enter to close'
        Read-Host | Out-Null
    }
}
exit $exitCode
