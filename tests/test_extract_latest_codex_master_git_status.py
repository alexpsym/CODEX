import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _installer_script_path() -> Path:
    modern = Path("INSTALL.bat")
    legacy = Path("ExtractLatestCodexMaster.bat")
    return modern if modern.exists() else legacy


def classify_status(status_text: str, allowed_root_generated_files=None):
    allowed_root_generated_files = allowed_root_generated_files or []
    result = {"IsOnlyAllowed": True, "DisallowedLines": []}
    if status_text is None or status_text.strip() == "":
        return result

    lines = [ln for ln in status_text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    for line_raw in lines:
        if len(line_raw) < 4:
            result["IsOnlyAllowed"] = False
            result["DisallowedLines"].append(line_raw)
            continue

        xy = line_raw[:2]
        path_raw = line_raw[3:]

        if path_raw.strip() == "":
            result["IsOnlyAllowed"] = False
            result["DisallowedLines"].append(line_raw)
            continue

        if xy not in {"??", " M"}:
            result["IsOnlyAllowed"] = False
            result["DisallowedLines"].append(line_raw)
            continue

        path = path_raw.strip()
        if path.startswith('"') and path.endswith('"') and len(path) >= 2:
            path = path[1:-1]
        p = path.replace("\\", "/")

        if p.strip() == "":
            result["IsOnlyAllowed"] = False
            result["DisallowedLines"].append(line_raw)
            continue

        is_allowed_generated_root_file = ("/" not in p) and (p in allowed_root_generated_files)
        allowed = (
            p == ".env"
            or p == "env.env"
            or p in {"watchlist.json", "state_manifest.json", "stateManifest.json", "state_backup.json"}
            or p in {"journal", "journal/"}
            or p.startswith("journal/")
            or (p.startswith("bybit_monitor/") and p.endswith(".json"))
            or (p.startswith("oanda_monitor/") and p.endswith(".json"))
            or p in {"render/data", "render/data/"}
            or p.startswith("render/data/")
            or p in {"render/uploads", "render/uploads/"}
            or p.startswith("render/uploads/")
            or is_allowed_generated_root_file
        )

        if "__pycache__" in p or not allowed:
            result["IsOnlyAllowed"] = False
            result["DisallowedLines"].append(line_raw)

    return result


def test_classifier_source_preserves_leading_xy_columns():
    text = _installer_script_path().read_text(encoding="utf-8")
    assert "$lineRaw.Substring(0,2)" in text
    assert "$line = $lineRaw.Trim()" not in text


def test_allows_empty_status():
    assert classify_status("")["IsOnlyAllowed"] is True


def test_allows_required_runtime_entries_including_leading_space_m():
    status = "\n".join(
        [
            "?? bybit_monitor/runtime_status.json",
            "?? oanda_monitor/custom_alerts.json",
            "?? render/uploads/",
            "?? journal/Bybit-UM-USDTPerp-TradeHistory-template.csv",
            " M render/data/monthly_aud_revaluation.json",
            " M render/data/trade_contexts.json",
            ' M "journal/OANDA DEMO.xlsx"',
        ]
    )
    result = classify_status(status)
    assert result["IsOnlyAllowed"] is True


def test_rejects_unsafe_statuses_and_paths():
    cases = [
        " M render/master_service.py",
        "M  bybit_monitor/custom_alerts.json",
        "M  render/data/monthly_aud_revaluation.json",
        " D render/data/monthly_aud_revaluation.json",
        "R  old -> new",
        "?? render/master_service.py",
        "?? render/data/__pycache__/x.pyc",
        "??",
        "M",
    ]
    for case in cases:
        result = classify_status(case)
        assert result["IsOnlyAllowed"] is False, case


def test_allowed_generated_root_file_example():
    result = classify_status("?? Local Trading Tools.exe", ["Local Trading Tools.exe"])
    assert result["IsOnlyAllowed"] is True


def test_allows_relocated_state_root_files_only():
    ok = classify_status("\n".join(["?? watchlist.json", " M state_manifest.json", "?? state_backup.json"]))
    assert ok["IsOnlyAllowed"] is True
    bad = classify_status("?? random.json")
    assert bad["IsOnlyAllowed"] is False


def test_preserve_local_files_from_backup_includes_relocated_root_state_files():
    text = _installer_script_path().read_text(encoding="utf-8")
    assert "function Preserve-LocalFilesFromBackup" in text
    for file_name in ("watchlist.json", "state_manifest.json", "stateManifest.json", "state_backup.json"):
        assert file_name in text


def test_fast_forward_state_preservation_is_local_change_aware_and_failure_safe():
    text = _installer_script_path().read_text(encoding="utf-8")
    helper_start = text.index(
        "function Move-LocalStateFilesRemovedByTargetBeforeGitUpdate"
    )
    helper_end = text.index("function ConvertTo-NativeArgumentString", helper_start)
    helper = text[helper_start:helper_end]
    assert "'status'," in helper
    assert "'--porcelain'," in helper
    assert "'--untracked-files=all'," in helper
    assert "$_ -match '^( M|\\?\\?) '" in helper
    assert "'ls-tree'," in helper
    assert "'ls-files'," in helper
    assert "$TargetRef" in helper
    assert "$writeEmptyWatchlistTombstone = $false" in helper
    assert "authoritative empty tombstone" in helper

    fast_forward_start = text.index(
        '$ffBlockerBackupDir = Join-Path $DestinationRoot "CODEX-master-fastforward-blockers-$ffTimestamp"'
    )
    fast_forward_end = text.index("$headAfterFastForward =", fast_forward_start)
    fast_forward = text[fast_forward_start:fast_forward_end]
    assert "$ffUpdateCompleted = $false" in fast_forward
    assert "try {" in fast_forward
    assert "} finally {" in fast_forward
    try_index = fast_forward.index("try {")
    finally_index = fast_forward.index("} finally {")
    assert try_index < fast_forward.index(
        "Move-CheckoutBlockingUntrackedFilesBeforeGitUpdate"
    ) < finally_index
    assert try_index < fast_forward.index(
        "Move-LocalStateFilesRemovedByTargetBeforeGitUpdate"
    ) < finally_index
    assert fast_forward.index("} finally {") < fast_forward.index(
        "Preserve-LocalFilesFromBackup"
    )
    assert (
        "$ffPreservedFilesExist = Test-Path -LiteralPath $ffRestoreRoot -PathType Container"
        in fast_forward
    )
    assert "if ($ffMovedBlockers -gt 0)" not in fast_forward
    assert "if ($ffUpdateCompleted) {" in fast_forward
    assert "Fast-forward failed; restored preserved local files and retained backup" in (
        fast_forward
    )


def test_installer_preserves_an_authoritative_empty_watchlist_over_stale_checkout(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required to execute the installer preservation helper")

    backup_dir = tmp_path / "backup"
    new_repo_dir = tmp_path / "CODEX-master"
    backup_dir.mkdir()
    new_repo_dir.mkdir()
    authoritative = {
        "watchlist.json": [],
        "state_manifest.json": {
            "watchlist": {
                "key": "watchlist",
                "sha256": "authoritative-empty",
            }
        },
        "state_backup.json": {"watchlist": []},
    }
    stale = {
        "watchlist.json": ["STALEUSDT"],
        "state_manifest.json": {"watchlist": {"sha256": "stale"}},
        "state_backup.json": {"watchlist": ["STALEUSDT"]},
    }
    for name, payload in authoritative.items():
        (backup_dir / name).write_text(json.dumps(payload), encoding="utf-8")
    for name, payload in stale.items():
        (new_repo_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    installer = _installer_script_path().read_text(encoding="utf-8")
    start = installer.index("function Preserve-LocalFilesFromBackup")
    end = installer.index("function Remove-OldInstallLogs", start)
    helper_source = installer[start:end]
    backup_literal = str(backup_dir).replace("'", "''")
    repo_literal = str(new_repo_dir).replace("'", "''")
    script = tmp_path / "preserve-state.ps1"
    script.write_text(
        "function Write-Section { param([string]$Message) }\n"
        + helper_source
        + "\n"
        + (
            "Preserve-LocalFilesFromBackup "
            f"-BackupDir '{backup_literal}' -NewRepoDir '{repo_literal}'\n"
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for name, payload in authoritative.items():
        assert json.loads((new_repo_dir / name).read_text(encoding="utf-8")) == payload


@pytest.mark.parametrize(
    ("watchlist", "state_mode"),
    [
        pytest.param([], "all", id="authoritative-empty"),
        pytest.param(["BTCUSDT"], "all", id="legitimate-symbol"),
        pytest.param([], "none", id="untouched-stale-bootstrap"),
        pytest.param([], "aggregate_only", id="mixed-status-stale-aggregate"),
    ],
)
def test_fast_forward_migration_handles_local_state_when_target_untracks_files(
    tmp_path: Path,
    watchlist: list[str],
    state_mode: str,
) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    git = shutil.which("git")
    if not powershell or not git:
        pytest.skip("PowerShell and Git are required for the updater migration regression")

    def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git, *args],
            cwd=repo,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )

    remote = tmp_path / "remote.git"
    publisher = tmp_path / "publisher"
    installed = tmp_path / "installed"
    subprocess.run(
        [git, "init", "--bare", str(remote)],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    publisher.mkdir()
    run_git(publisher, "init", "-b", "master")
    run_git(publisher, "config", "user.email", "installer-test@example.invalid")
    run_git(publisher, "config", "user.name", "Installer Test")
    (publisher / ".gitignore").write_text(
        "state_backup.json\nstate_manifest.json\n",
        encoding="utf-8",
    )
    stale_payloads = {
        "watchlist.json": ["STALEUSDT"],
        "state_manifest.json": {"watchlist": {"sha256": "stale"}},
        "state_backup.json": {
            "watchlist": ["STALEUSDT"],
            "healthy_state": {"keep": True},
        },
    }
    for name, payload in stale_payloads.items():
        (publisher / name).write_text(json.dumps(payload), encoding="utf-8")
    run_git(
        publisher,
        "add",
        "-f",
        ".gitignore",
        "watchlist.json",
        "state_manifest.json",
        "state_backup.json",
    )
    run_git(publisher, "commit", "-m", "tracked bootstrap state")
    run_git(publisher, "remote", "add", "origin", str(remote))
    run_git(publisher, "push", "-u", "origin", "master")
    subprocess.run(
        [git, "clone", "--branch", "master", str(remote), str(installed)],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )

    run_git(
        publisher,
        "rm",
        "watchlist.json",
        "state_manifest.json",
        "state_backup.json",
    )
    (publisher / ".gitignore").write_text(
        "watchlist.json\nstate_backup.json\nstate_manifest.json\nstateManifest.json\n",
        encoding="utf-8",
    )
    run_git(publisher, "add", ".gitignore")
    run_git(publisher, "commit", "-m", "move runtime state out of source control")
    run_git(publisher, "push", "origin", "master")

    authoritative_payloads: dict[str, object] = {}
    before_bytes: dict[str, bytes] = {}
    if state_mode == "all":
        canonical_blob = json.dumps(
            watchlist,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        authoritative_payloads = {
            "watchlist.json": watchlist,
            "state_manifest.json": {
                "watchlist": {
                    "key": "watchlist",
                    "sha256": hashlib.sha256(canonical_blob).hexdigest(),
                }
            },
            "state_backup.json": {
                "watchlist": watchlist,
                "healthy_state": {
                    "keep": True,
                    "alerts": ["alert-1"],
                },
            },
        }
        for name, payload in authoritative_payloads.items():
            (installed / name).write_text(json.dumps(payload), encoding="utf-8")
        before_bytes = {
            name: (installed / name).read_bytes()
            for name in authoritative_payloads
        }
    elif state_mode == "aggregate_only":
        authoritative_payloads = {
            "state_backup.json": {
                "watchlist": ["STALEUSDT"],
                "healthy_state": {
                    "keep": True,
                    "alerts": ["unrelated-runtime-change"],
                },
            }
        }
        for name, payload in authoritative_payloads.items():
            (installed / name).write_text(json.dumps(payload), encoding="utf-8")
        before_bytes = {
            name: (installed / name).read_bytes()
            for name in authoritative_payloads
        }
    run_git(installed, "fetch", "origin", "master")
    changed_paths = {
        line[3:].strip()
        for line in run_git(installed, "status", "--porcelain").stdout.splitlines()
        if line.strip()
    }
    assert changed_paths == set(authoritative_payloads)

    installer = _installer_script_path().read_text(encoding="utf-8")
    move_start = installer.index(
        "function Move-LocalStateFilesRemovedByTargetBeforeGitUpdate"
    )
    move_end = installer.index("function ConvertTo-NativeArgumentString", move_start)
    move_helper = installer[move_start:move_end]
    preserve_start = installer.index("function Preserve-LocalFilesFromBackup")
    preserve_end = installer.index("function Remove-OldInstallLogs", preserve_start)
    preserve_helper = installer[preserve_start:preserve_end]
    backup_dir = tmp_path / "fast-forward-backup"
    script = tmp_path / "simulate-fast-forward.ps1"

    def ps_literal(value: object) -> str:
        return str(value).replace("'", "''")

    script.write_text(
        """
function Write-Section { param([string]$Message) }
function Invoke-GitText {
    param(
        [string]$GitExe,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [switch]$AllowFailure,
        [switch]$Quiet
    )
    Push-Location -LiteralPath $WorkingDirectory
    try {
        $output = @(& $GitExe @Arguments)
        if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
            throw "git command failed: $($Arguments -join ' ')"
        }
        return ($output -join [Environment]::NewLine)
    } finally {
        Pop-Location
    }
}
"""
        + move_helper
        + "\n"
        + preserve_helper
        + "\n"
        + (
            f"$gitExe = '{ps_literal(git)}'\n"
            f"$repoDir = '{ps_literal(installed)}'\n"
            f"$backupDir = '{ps_literal(backup_dir)}'\n"
            f"$expectedMoved = {3 if state_mode in {'all', 'aggregate_only'} else 2}\n"
            "$moved = Move-LocalStateFilesRemovedByTargetBeforeGitUpdate "
            "-GitExe $gitExe -RepoDir $repoDir -BackupDir $backupDir "
            "-TargetRef 'origin/master'\n"
            "if ($moved -ne $expectedMoved) { "
            "throw \"Expected $expectedMoved preserved state files; got $moved\" }\n"
            "Push-Location -LiteralPath $repoDir\n"
            "try {\n"
            "    & $gitExe checkout master\n"
            "    if ($LASTEXITCODE -ne 0) { throw 'git checkout failed' }\n"
            "    & $gitExe merge --ff-only origin/master\n"
            "    if ($LASTEXITCODE -ne 0) { throw 'git fast-forward merge failed' }\n"
            "} finally {\n"
            "    Pop-Location\n"
            "}\n"
            "if ($moved -gt 0) {\n"
            "    $restoreRoot = Join-Path $backupDir 'checkout-blockers'\n"
            "    Preserve-LocalFilesFromBackup -BackupDir $restoreRoot -NewRepoDir $repoDir\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert run_git(installed, "rev-parse", "HEAD").stdout.strip() == run_git(
        installed, "rev-parse", "origin/master"
    ).stdout.strip()
    assert (
        run_git(
            installed,
            "ls-files",
            "--",
            "watchlist.json",
            "state_manifest.json",
            "state_backup.json",
        ).stdout.strip()
        == ""
    )
    assert run_git(installed, "status", "--porcelain").stdout.strip() == ""
    for name, expected in before_bytes.items():
        assert (installed / name).read_bytes() == expected
        assert json.loads((installed / name).read_text(encoding="utf-8")) == (
            authoritative_payloads[name]
        )
    if state_mode != "all":
        assert json.loads(
            (installed / "watchlist.json").read_text(encoding="utf-8")
        ) == []
        manifest = json.loads(
            (installed / "state_manifest.json").read_text(encoding="utf-8")
        )
        canonical_empty = json.dumps(
            [],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert manifest["watchlist"]["sha256"] == hashlib.sha256(
            canonical_empty
        ).hexdigest()
        assert not (installed / "stateManifest.json").exists()
        if state_mode == "none":
            assert not (installed / "state_backup.json").exists()


def test_invoke_git_text_uses_process_start_info_and_separate_stderr():
    text = _installer_script_path().read_text(encoding="utf-8")
    assert "function Invoke-GitText" in text
    assert "System.Diagnostics.ProcessStartInfo" in text
    assert "$psi.RedirectStandardError = $true" in text
    assert "& $GitExe @Arguments 2>&1" not in text


def test_backup_diagnostics_use_best_effort_helper():
    text = _installer_script_path().read_text(encoding="utf-8")
    assert "function Write-GitDiagnosticFile" in text
    assert "WARNING: Could not write backup diagnostic" in text
    assert "git-status-before-reset.txt" in text
    assert "git-log-local-ahead.txt" in text
    assert "local-changes.patch" in text
    assert "local-staged-changes.patch" in text


def test_classifier_allows_modified_state_backup_file():
    result = classify_status(" M state_backup.json")
    assert result["IsOnlyAllowed"] is True


def test_classifier_rejects_modified_render_master_service():
    result = classify_status(" M render/master_service.py")
    assert result["IsOnlyAllowed"] is False


def test_invoke_git_text_does_not_trim_stdout_leading_columns():
    text = _installer_script_path().read_text(encoding="utf-8")
    invoke_git_text_start = text.index("function Invoke-GitText")
    invoke_git_text_end = text.index("function Remove-TrailingLineTerminators")
    invoke_git_text_body = text[invoke_git_text_start:invoke_git_text_end]
    assert "$stdoutTask.Result.Trim()" not in invoke_git_text_body
    assert "Remove-TrailingLineTerminators" in text
    assert "$stdout = Remove-TrailingLineTerminators -Text $stdoutTask.Result" in text


def test_classifier_allows_bybit_custom_alerts_modified_worktree():
    result = classify_status(" M bybit_monitor/custom_alerts.json")
    assert result["IsOnlyAllowed"] is True


def test_classifier_allows_leading_space_first_line_mixed_allowed_runtime_entries():
    status = "\n".join(
        [
            " M bybit_monitor/custom_alerts.json",
            " M render/data/monthly_aud_revaluation.json",
            " M render/data/monthly_aud_revaluation_state.json",
            " M state_backup.json",
            "?? bybit_monitor/runtime_status.json",
            "?? render/data/trading_journal.json",
        ]
    )
    result = classify_status(status)
    assert result["IsOnlyAllowed"] is True
