# Page Video Downloader

Double-click **Download Videos.bat**, paste one HTTP(S) webpage URL at
`Paste the webpage URL and press Enter:`, then press Enter. Downloads run one at
a time. Read the completion summary and press Enter to close PowerShell.

Completed files go directly into **C:\Users\User\Downloads\New folder**.
The tool creates that directory when necessary. No destination selection or
per-page subdirectories are used. Keep all files in this tool folder together;
the launcher works regardless of its initial working directory.

## Automatic first-run setup

Requires 64-bit Windows 10/11, built-in Windows PowerShell 5.1, Internet access,
and permission to write this folder and the destination. No administrator rights,
accounts, API keys, installed Python/Node, or PowerShell 7 are needed.

First launch downloads portable dependencies, which can take several minutes
and several hundred MB. Subsequent launches reuse them:

* Node.js: latest LTS Windows x64 ZIP from nodejs.org; SHA-256 checked.
* yt-dlp: official Windows executable from its GitHub releases; SHA-256 checked.
  The standalone build includes the EJS solver.
* Deno: official portable executable from denoland/deno releases, for current
  yt-dlp JavaScript challenge support.
* FFmpeg/ffprobe: Gyan essentials ZIP, endorsed by FFmpeg's download page;
  SHA-256 checked.
* Playwright: current stable package from the official npm registry, saved with
  its package lock; its matching Chromium headless shell from Microsoft's CDN.

Everything is stored under `.runtime` inside this folder. Its temporary files,
browser, npm and Deno caches, dependency archives, completion index and sanitized
last-run summary are ignored by Git. PATH and execution policy are never changed
permanently. The launcher uses execution-policy bypass only for its child process.
Checksums fetched over HTTPS detect corrupt/mismatched downloads; they are not
an independent publisher-signature verification system.

## Discovery and downloading

The tool inventories HTML video/source tags, iframe/object/embed players and
common lazy URL attributes. It also checks a fresh Chromium session, inspects
embedded frames, activates lazy media sources, scrolls the page, attempts muted
HTML video playback, and observes MP4/HLS/DASH requests. Page-level yt-dlp
extraction supplements this inventory. Ordinary links are never crawled.

Repeated URLs and extracted source identities are deduplicated. Sources within
one HTML video element are treated as quality alternatives. Embedded generic
multi-video pages may expand into multiple files; channel and external playlist
results are refused. yt-dlp is always passed `--no-playlist`.

Downloads use yt-dlp's best video/audio selection, merge separate streams, prefer
MP4 when compatible, and retain MKV or another suitable container when needed.
There is no transcoding. Names contain page ordering, a bounded Windows-safe
title and a stable hashed source identifier. The source identity keeps distinct
clips with identical titles separate. Source ordering is best effort for nested
or dynamically rearranged layouts.

Referrers, browser user agent and observed Origin headers are carried forward.
No installed browser profile, credentials or cookies are imported. No cookies or
authorization headers are saved or exported. Some publicly accessible sessions
still require cookies; those sites may fail under this deliberate restriction.

ffprobe must confirm a final video stream, positive duration, and audio when
extraction indicated audio before completion is recorded. Existing completed
files are preserved and checked before skipping. Missing or changed files are
retried. yt-dlp partial files remain resumable, and failed items remain retryable.
Ctrl+C cancels discovery/download work; partial files are retained. A file in
progress is never entered as completed. Multiple simultaneous instances are not
supported: wait for one run to finish before launching another.

The terminal reports discovered/downloaded/skipped/failed counts, brief failure
reasons, known unresolved players and coverage warnings, and the save directory.
`.runtime/last-summary.json` keeps these diagnostics without source URLs,
query tokens, headers, cookies or raw extraction errors. `.runtime/completed.json`
contains only hashed identities, output stems, paths and sizes. Do not edit this
index during a run. A corrupt index is reported rather than silently discarded.

## Limits

This is not universal website support. DRM, paywalls, authentication, anti-bot
challenges, unsupported players and private resources can prevent downloads.
There is no DRM or access-control bypass. Arbitrary custom play buttons, consent
dialogs, infinite feeds, shadow-DOM players, and scripts requiring user interaction
can hide videos. Discovery waits briefly for scripts and stops after 100 scroll
steps; it cannot prove that a site contains no undiscovered videos. Network-only
streams with no player identity are grouped conservatively by frame and directory;
unusual multi-clip/quality arrangements may need site-specific support. Different
signed CDN URLs without a common player identity may evade deduplication.

Known inaccessible players are reported, not silently treated as successful.
Unknown players cannot be reported individually if the page exposes no evidence
of them. A clean summary means every discovered item succeeded or was skipped,
not proof of universal coverage. Long/live streams may run indefinitely; use
Ctrl+C if appropriate. Only download content you are entitled to save.

## Dependency maintenance

Dependencies are reused, not updated silently on every launch. To refresh them,
close the tool, rename `.runtime` to `.runtime-old`, then launch again. After setup,
copy `completed.json` from the old directory to the new `.runtime` before the next
download to preserve skip history. Preserve any partial downloads in the save
directory. Do not put runtime directories or binaries in Git. When changing a
Playwright package, install its matching Chromium revision too. Setup failures
show a clear message and can be retried; an antivirus quarantine may require
reviewing the quarantined dependency rather than repeated downloads.

Upstream references checked for this implementation:
[yt-dlp](https://github.com/yt-dlp/yt-dlp),
[EJS runtimes](https://github.com/yt-dlp/yt-dlp/wiki/EJS),
[Node distributions](https://nodejs.org/dist/index.json),
[Playwright browsers](https://playwright.dev/docs/browsers),
[FFmpeg downloads](https://ffmpeg.org/download.html),
[Gyan builds](https://www.gyan.dev/ffmpeg/builds/),
[ffprobe](https://ffmpeg.org/ffprobe.html).

## Controlled verification

Developer check (after setup): `node --test test-downloader.cjs`, or use the
portable `.runtime\node\node.exe` instead of `node`. It creates temporary fixtures
only under this tool's `.runtime` folder, serves a local multi-video test page,
and uses the real yt-dlp/FFmpeg/ffprobe/Chromium binaries. It checks separate
playable video+audio outputs, duplicate references, a failed source among
successes, page order, iframe/lazy/JavaScript HLS discovery, query parameters,
paths containing spaces, repeat skips and re-downloading a deleted completion.
It also asserts that an unrelated channel link was never requested.

This controlled page is not a test of your actual target website: none was
supplied. See `VERIFICATION.md` for the recorded results and launcher limitations.
