# Verification record

Verified on Windows with Windows PowerShell 5.1 on 2026-10-15.

The focused test command using portable Node completed with **3 passed, 0 failed**
in 68.7 seconds. The controlled local HTTP page exposed five available clips and
one deliberately missing clip. It was not an actual target website.

| Check | Observed result |
| --- | --- |
| Multiple embeds | 6 distinct items; 5 separate final playable files, 1 explicit 404 failure |
| Video/audio verification | ffprobe confirmed both streams and positive duration in all 5 files |
| HLS | JavaScript-created HLS video discovered after scrolling and downloaded |
| DASH | Separate video/audio representations downloaded and merged successfully |
| Nested iframe | Child video discovered and downloaded in parent page order |
| Lazy source | `data-src` video discovered automatically |
| Duplicate references | Repeated first-video reference produced only one file |
| Quality alternatives | Focused regression selected 1080p over 360p as one video |
| Failure isolation | Deliberate 404 reported; remaining videos still completed |
| Repeat run | 5 skipped, 0 downloaded; failed source remained retryable |
| Deleted output | 4 skipped, 1 downloaded after removing one test-owned completion |
| Paths with spaces | Destination and state directories contained spaces |
| Query parameters | HTTP requests preserved `&`, `%25` and `%22` query values |
| Crawl boundary | Unrelated channel link was never requested |
| BAT invocation | Exact BAT ran via cmd from another working directory containing spaces |
| Input-as-data | Shell-like invalid input was rejected without executing its command text |
| PowerShell | 5.1 parsing, automatic dependency setup and subsequent reuse passed |
| Git exclusions | Portable binaries, browser, caches, test media and delivery adapter ignored |

Measured first-run result: discovered 6, downloaded 5, skipped 0, failed 1;
one known unresolved player (the deliberately missing source). The completion
index and last-run diagnostics contain no source URLs or request headers.
Runtime evidence is under `.runtime/controlled test GHI9v6/verification.json`;
generated files and local diagnostics are intentionally not committed.

Dependencies exercised: yt-dlp 2026.08.19, Deno 2.9.7, FFmpeg/ffprobe 9.0.2,
Playwright 1.64.1 and matching Chromium headless shell revision 1248.

Remaining unverified: Explorer GUI double-click itself and visually confirming
that the interactive terminal stays open until Enter. BAT/PowerShell invocation
and close-prompt text were exercised using redirected input, which cannot prove
interactive window lifetime. The production destination was not populated by
tests; fixture downloads stayed inside this tool's runtime directory. Real
website compatibility, protected resources, live streams, interrupted network
resume and interactive Ctrl+C behavior were not established by these tests.

No target website URL was supplied. These results do not claim universal support
or prove that every possible JavaScript player can be discovered.
