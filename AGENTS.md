# Repository Instructions for Codex

This file contains persistent Codex instructions for this repository.

Keep `custom instructions/CODEX_BRIEF_INSTRUCTIONS.txt` as ChatGPT-only instructions. Do not rely on that file as Codex persistent instructions for repository work.

## Mandatory completion workflow

* After every successful code fix, edit, revision, migration or completed task, write the final summary to `summary/CODEX_SUMMARY_latest.txt`.
* After writing the summary, always run the repository-root `PATCH_DESKTOP_PUSH.bat`.
* This requirement applies automatically without the user needing to request a commit or push.
* Only skip the commit and push when the user explicitly says not to push.
* Do not report the task as complete until `PATCH_DESKTOP_PUSH.bat` finishes successfully and verifies that local HEAD matches `origin/master`.
* If the script fails, report the exact failure and leave the task marked incomplete.
* Never claim that work is complete when changes remain only on the local machine.
* Do not replace this workflow with manual Git commands unless `PATCH_DESKTOP_PUSH.bat` itself is broken.
* Do not commit secrets, environment files, logs, caches, runtime data or local-state files.

## Machine-safety boundaries

Operate only inside this repository and its explicitly documented output locations.

Do not modify or delete Windows system files, the Windows registry, Windows services, scheduled tasks, startup configuration, environment variables, unrelated user-profile files, unrelated repositories, or files outside the approved project paths.

Do not run recursive deletion commands, disk-management commands, registry commands, system-administration commands, force pushes, Git history rewrites, or commands that discard uncommitted work.

Do not use git reset --hard, git clean -fd, git push --force, git push --force-with-lease, or equivalent destructive commands.

If completing the task genuinely requires any prohibited action, stop and report exactly what is required instead of performing it.