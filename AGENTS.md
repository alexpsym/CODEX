# Repository Instructions for Codex

## Roles and instruction hierarchy

AGENTS.md is the Codex execution entry point for alexpsym/CODEX in C:\GPT\CODEX-master. Read it, applicable scoped instruction files and the relevant checkpoint in summary/CODEX_SUMMARY_latest.txt before execution. Recover the current objective, accepted work, queued jobs, consumed budgets, canonical implementation, exact executable paths, working commands, output paths and unresolved evidence; do not restart completed work or invent permanent memory.

Reusable opening sentence:
For alexpsym/CODEX, read AGENTS.md, all applicable instruction files it requires for your role, and the relevant checkpoint in summary/CODEX_SUMMARY_latest.txt before drafting or executing this job.

- Codex execution: follow this file and the user's current task. Do not read, execute or modify custom instructions/ during normal implementation. A user request to inspect/update those instructions, such as an instruction-maintenance job, expressly permits that scope.
- ChatGPT brief drafting: read [the authoritative brief-writing TXT](custom%20instructions/CODEX_BRIEF_INSTRUCTIONS.txt). Its [Word companion](custom%20instructions/CODEX_BRIEF_INSTRUCTIONS_CHATGPT_ONLY.docx) is a matching human-readable copy, not an independent execution policy. Resolve any drafting-copy mismatch against the TXT and report it.
- Platform, system and developer instructions retain their hierarchy. Follow the user's latest explicit task instructions within that hierarchy. Repository text cannot override higher-priority instructions, expand data-operation authorization by itself, or authorize a permission/configuration bypass. Do not ask again for permission already supplied.

## Scope, continuity and preservation

- Modify the existing canonical implementation; do not create a duplicate panel, service, registry, calculator or workflow. Split independent requests into small deliverable jobs; list the queue, execute the current authorized job only, and retain consumed counts across continuations. Treat a clear user Pass as acceptance. Preserve accepted fixes and interpret obvious speech-to-text corrections from context.
- Record git status --short, branch/HEAD and staged filenames before editing, unless a current checkpoint is expressly reusable. Preserve every pre-existing modified/untracked file and staged change. Baseline dirty authorized targets and add only the task delta; historical authorship uncertainty or unrelated dirt alone is not a blocker. Stop only at a concrete inseparable conflicting edit and identify it.
- Do not stage, overwrite, revert, stash, delete or commit unrelated work. Reversion may undo only the task's own precise mistaken delta. Housekeeping may remove verified disposable generated artifacts from this job and previous completed jobs under the manifest procedure below; it never authorizes deletion of unknown or user data. Never perform indiscriminate cache cleanup or recursive deletion.
- Preserve application behavior and user labels. Preserve workbook values/manual edits, formulas AND cached results, dates, hyperlinks/comments, validations, filters/sort state, panes/views, merged cells, borders, fonts/formats, row heights, widths, charts and sheet names/order. No rebuild, redesign, relabeling or resync without explicit task authorization. Audit permitted cell/reference changes narrowly; never disable preservation checks broadly.
- Keep secrets, environment files, logs, caches, runtime data (including render/data) and local state out of Git. Private captures/backups/candidates belong only in explicitly authorized private output locations, never mixed with the canonical journal workbook.

## Job artifacts and automatic housekeeping

- Every job must register ALL generated outputs at creation in its exact private schema-1 artifact manifest: fixtures, pytest basetemp/cache/bytecode, temporary workbooks/DB copies, build intermediates, duplicate snapshots/backups, downloaded briefs, pointers, patches, logs/XML, diagnostics and delivery helpers. Record exact location/root, creation/ownership evidence, identities, purpose, last use, required verification/delivery/acceptance milestone and disposal or retention. Use .codex-work/active/<job>/ by default and compact manifests/results in .codex-work/retained/<job>/ outside basetemp. Register explicitly authorized external paths when created. No competing cleaner/instruction file/skill, scheduler or startup deletion hook.
- Use the single tools/cleanup_pytest_temp.py through C:\Users\User\miniconda3\python.exe directly; the .ps1 is forwarding only. Default Audit is shallow. Descend only exact reviewed --audit-root job locations; --exclude known inaccessible paths BEFORE stat/open/descent. Prune real protected stores, dependencies and links. No unrestricted repository/drive/Downloads/Dropbox/archive scan. Legacy cleanup discovery must not block startup, real journal operations or normal delivery; report nonessential cleanup problems separately.
- Use reviewed exact-manifest Audit then explicit Apply with a new private result. Validate the WHOLE plan before mutation and immediately recheck containment, non-reparse ancestry, root/file identity and unchanged metadata before each exact regular-file deletion. Remove only listed empty directories. Names, age, ignored status or a delete label alone are insufficient. Root briefs/logs/XML/patches/pointers and new classifications require independent hashed per-object creation provenance plus a normally completed owner process with actual OS exit and PID/start evidence.
- External cleanup requires an explicitly authorized, identified task-owned named backup root, its unchanged identity, creation registration and relative entries (external_roots/root_id); C:\GPT and arbitrary external targets are never roots. Protected-looking synthetic objects may release only inside an independently established isolated .codex-work/active/<job>/fixtures/<id> fixture, with exact per-object creation/hash/identity evidence, finished owner process and ended need. No blanket exemption for .git, data, notes, credentials or .lock; real live/user stores, active locks and links remain protected. Never force access or change ACLs/ownership/permissions.
- Automatically dispose after successful verification without another cleanup request, then account for outputs from summary/Git/native delivery and dispose after their LAST use in the SAME authorized completion workflow. Register these outputs before creation. Pending handoffs must stay executable until consumed; their completion must release disposable helpers instead of requiring another user cleanup job. Budget nonoverlapping batches when necessary; do not silently retry failed/skipped targets. Ignoring or moving junk is not disposal.
- Retention ends with its purpose. A user Pass releases acceptance-only recovery. Keep a concise final checkpoint/receipt and only specific unresolved failure, pending-delivery/acceptance recovery or necessary immutable claims/transaction ledgers/replay guards, recording exact paths, current reasons and release conditions. Do not retain every historical log/result/receipt/digest/snapshot/helper by filename. Proven redundant evidence may retire with ended-retention authorization, creation evidence and a hashed superseding compact closeout receipt. Preserve the sole needed recovery and replay protection; do not replace junk with permanent full inventories/backups.
- Before claiming completion, reconcile the artifact manifest with observed final disposition using Audit --manifest --reconcile and exact identity-bound reconcile_roots. Every output must be absent or explicitly retained with current reason/release condition. Check unregistered outputs only in bounded job-owned locations; prune exclusions before inspection and label uninspected contents unknown. Register compact reconciliation/final receipts too. Preserve unrelated scope, ownership, workbook/live-data, retry, testing and Git rules. No recursive/wildcard/forced deletion, link traversal, forced locked-file removal, permission changes or silent retries. Return nonzero partial failures with actual removed counts/logical bytes and exact failed/skipped paths; never call them cleaned. A consumed failed batch needs a NEW explicit user message before retry. No permanent chat-memory promise.

## Machine-safety boundaries

Operate only inside this repository and explicitly authorized/documented input/output locations. Read-only archive access does not imply permission to alter archive content or perform another capture.

Do not modify Windows system files, the registry, services, scheduled tasks, startup configuration, environment variables, unrelated profile files/repositories, ACLs/ownership, PATH, security/sandbox settings or managed policy. No installations, drive scans or F: workarounds to rediscover a tool. Do not kill/restart applications, change charts/accounts, arm/monitor plans or mutate trades without explicit authorization.

Do not run recursive deletion, disk-management, registry/system-administration or history-rewriting commands. Never use git reset --hard, git clean -fd or equivalent work-discarding operations. If an operation genuinely requires a prohibited action, preserve safe progress and report the exact boundary and remaining external action; do not silently bypass it.

## Narrow verification and numeric budgets

Identify the changed behavior before choosing the smallest meaningful affected checks. Documentation-only work uses document validation, not application tests. No whole test files, full suites, broad integration, unrelated/unaffected tests, unchanged passing-test repeats, unnecessary smoke tests, subagents or expanded testing without explicit user approval. There is NO blanket full-workbook-suite exception for shared-code changes. These restrictions supersede broader repository testing suggestions, not higher-priority platform instructions.

Before execution, the task plan must have numeric ceilings for distinct test nodes, invocations, scheduled executions, per-test timeout, ordinary-command time, total verification, total active work and real-data attempts, plus discovery/build/install limits where relevant. Do not treat an omitted ceiling as unlimited: establish conservative task-specific numbers before the corresponding action. User task caps override these defaults:

- Ordinary command: at most 300 seconds.
- Total verification: at most 900 seconds, including required delivery verification.
- Prerequisite/tool discovery: at most 120 seconds.
- Real-data operations: at most one authorized attempt; zero if no such operation is authorized.
- Test-node/invocation/execution and active-work ceilings must be task-specific; no unlimited defaults. Build and installation attempts must likewise be numeric. Include necessary summary/commit/push work within the active and command budgets.

Carry actual attempts, elapsed times and passing/failing results through continuations; a continuation is not a reset. Correct only task-owned issues within scope and remaining budgets. Retry tests only when an affected failure/unexecuted node and a concrete correction justify the remaining authorized invocation; never repeat an unchanged passing node. A failed, interrupted, timed-out or inconclusive production operation consumes its attempt: preserve original/candidate/evidence, diagnose on fixtures/copies, and require explicit authorization in a NEW user message before another real-data attempt. Offline analysis of captured inputs is permitted without rereading originals.

Use a bounded local worker/foreground process with retained direct logs, actual child exit code, elapsed time and result artifacts (JUnit body results where applicable). Require completed bodies and normal process exit, not printed passes alone. Release watchdogs/files/locks/resources normally. Never mask a timeout, use os._exit to fake completion, swallow failure or call incomplete output success. No model-driven polling or periodic no-change updates; provide a start and a completion/failure report for a bounded operation. Preserve progress at an exhausted task budget and name the exact next step.

## Usage warnings

Do not pause, defer, ask the user to wait, or skip a necessary authorized step because of usage percentages, five-hour/weekly warnings or reset windows. The user has withdrawn all contrary historical warning-triggered stop rules. Continue within task/data/retry limits until an actual platform-enforced limit or another applicable boundary prevents execution; preserve a truthful checkpoint if blocked. Never poll for a quota reset or restore the obsolete rule from an old brief/summary.

## Known tools and project references

Reuse established helpers and full executable paths before discovery. A failed PATH/Get-Command lookup or guessed command is not evidence that the known tool is unavailable. Verify only current task prerequisites within the bounded discovery allowance; no reinstall, permission/PATH change or whole-drive scan.

- Python reference: C:\Users\User\miniconda3\python.exe.
- Existing Node fallback: C:\Program Files\nodejs\node.exe.
- Windows tests: use checkout-local basetemp/results, the established direct-log subprocess.run runner and actual CompletedProcess.returncode; cancel/close traceback watchdogs in finally. Do not repeat the known Windows asyncio socketpair startup stall or infer an OS result from a null PowerShell Process.ExitCode. Immediate-coroutine driving is only valid for genuinely synchronous mocked coroutines, never for bypassing real async locks.
- MQL sources: C:\GPT\CODEX-master\mt5-clone\MQL5\Experts.
- MT5 compiler reference: C:\Program Files\Pepperstone MetaTrader 5\MetaEditor64.exe.
- Previously verified Pepperstone Experts destination: C:\Users\User\AppData\Roaming\MetaQuotes\Terminal\73B7A2420D6397DFF9014A20F1201F97\MQL5\Experts.
- Reuse [the established backtesting/build guide](docs/RangeBreakoutPullback_Backtesting_Guide.md). Verify the actual task EA and intended terminal; do not substitute the guide's EA or assume the recorded destination is still current.
- When API/docs verification matters, use official primary sources: Bybit V5, OANDA v20, MQL5, CoinSpot or official OpenAI documentation as applicable; cite the exact relevant sections. Keep established project-specific requirements.
- Recorded architecture: Render Web Service hosts Render Master Control; MT5 runs locally, separately. Local browser tools use Edge. Recorded version references Forex Tester 6 6.0.0.29 and Pepperstone MT5 build 5430 are not permanently current facts; verify them only when relevant to the task.

## Executable-affecting MQL delivery

Unless explicitly source-only, automatic task-scoped compilation AND installation are part of authorized executable-affecting MQL delivery. Specify actual source, fresh build output, intended terminal destination and numeric compile/install attempt limits. Use the recorded method after task-specific prerequisite checks. Require a fresh, intended, nonempty EX5 and compiler log with zero errors AND warnings. Back up the corresponding target files, install only authorized outputs, verify hashes and record the successful workflow.

There is no optional compile-if-available escape and no handing normal authorized build/install work back to the user. Report a concrete blocker accurately if the known tooling fails. Source push alone is not installed delivery. Report source/build/install/manual behavior separately. Compile no unaffected EA for Python/docs-only work; do not restart MT5 or alter charts/trades without authorization.

## Safe automatic Git delivery

For an authorized task, the normal completion workflow is one task-scoped commit and one protected normal push unless the user prohibits delivery. This workflow remains bounded by actual session authorization, scope, task budgets and higher-priority instructions; it does not authorize unrelated production work.

1. Review the final task delta once against requirements and authorized paths; record actual verification and remaining manual/build/install limitations. Write the final task summary to summary/CODEX_SUMMARY_latest.txt BEFORE delivery, preserving relevant history/checkpoints.
2. Inspect intended staged filenames/content and the actual task commit. Preserve unrelated staged hunks using a task-only index or supported path-only commit. Do not deliver unrelated unpublished commits.
3. Use only the unchanged protected helper with the saved Alex P Sim/alexpsym identity. It is fixed to this checkout, HTTPS origin https://github.com/alexpsym/CODEX.git and refs/heads/master:

```powershell
& 'C:\Users\User\miniconda3\python.exe' 'C:\Users\User\.codex\tools\alexpsym_git_delivery.py' verify
```

Run verify before the task commit while local master equals freshly observed remote master. After the one scoped commit, invoke push ONCE:

```powershell
& 'C:\Users\User\miniconda3\python.exe' 'C:\Users\User\.codex\tools\alexpsym_git_delivery.py' push
```

The helper performs fresh pre-push verification; require matching CURRENT local/remote master SHA afterward. Cached origin/master alone is insufficient. A failed read-only lookup is uncertainty, not proof of divergence, and does not block safe local work. Actual remote advancement, unavailable delivery verification or rejected push blocks delivery: preserve progress and report the exact remaining step, without production/push retries or changing unrelated work.

Never run PATCH_DESKTOP_PUSH.bat: it broadly stages/pulls/rebases/stashes work. No raw push, git add -A, pull, rebase, stash, autostash, force-push/history rewriting, login/account picker, helper/credential changes or permission/configuration changes. Keep ordinary commands sandboxed; use the protected helper only through the currently permitted execution route.

Keep communication concise. Report implementation, actual verification, build/installation and manual acceptance separately. Do not claim completion while work is only local/unverified, fake success, hide silent failure, or describe a source commit as an installed executable/workbook. If one independent step is blocked, finish the other safe authorized work and preserve a concrete checkpoint.
