import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _windows_console_safe_creationflags() -> int:
    if os.name != "nt":
        return 0

    flags = getattr(subprocess, "DETACHED_PROCESS", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if flags == 0:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return flags


def _run_patch_desktop_push(script: Path, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["cmd.exe", "/d", "/c", "call", str(script)],
        cwd=cwd,
        text=True,
        capture_output=True,
        env=env,
        stdin=subprocess.DEVNULL,
        creationflags=_windows_console_safe_creationflags(),
        timeout=30,
    )


def test_patch_desktop_push_logs_and_stages_all_changes(tmp_path: Path):
    script = ROOT / "PATCH_DESKTOP_PUSH.bat"
    content = script.read_text(encoding="utf-8")
    assert "C:\\GPT\\CODEX-master" in content
    assert "C:\\GPT\\PATCH_DESKTOP_PUSH-latest.log" in content
    assert "This is not a Git checkout. Do not run git init blindly" in content
    assert "call git status --short --branch" in content
    assert "call git status --short --untracked-files=all" in content
    assert "git add -A -- ." in content
    assert 'call git reset -q -- "%%~P"' in content
    assert '"render/data"' in content
    assert '"state_backup.json"' in content
    assert "git add -u" not in content
    assert "-uno" not in content
    assert "PATCH_DESKTOP_PUSH_SUPPRESS_CONSOLE_TITLE" in content
    assert "PATCH_DESKTOP_PUSH_NONINTERACTIVE" in content
    assert "call git diff --cached --stat" in content
    assert "call git fetch origin master" in content
    assert "call git rev-parse origin/master" in content
    assert "pause >nul" in content.lower()
    if os.name == "nt":
        flags = _windows_console_safe_creationflags()
        assert flags & getattr(subprocess, "DETACHED_PROCESS", 0)
        assert flags & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    gpt_dir = tmp_path / "GPT"
    repo_dir = gpt_dir / "CODEX-master"
    fake_bin = tmp_path / "bin"
    fake_git_log = tmp_path / "git-args.txt"
    latest_log = gpt_dir / "PATCH_DESKTOP_PUSH-latest.log"
    repo_dir.mkdir(parents=True)
    fake_bin.mkdir()
    latest_log.write_text("old log should be overwritten", encoding="utf-8")
    (repo_dir / ".git").mkdir()

    fake_git = fake_bin / "git.bat"
    fake_git.write_text(
        "\n".join(
                [
                    "@echo off",
                    "echo %*>>\"%FAKE_GIT_LOG%\"",
                    "if \"%1\"==\"--version\" (echo git version 2.0.0& exit /b 0)",
                    "if \"%1\"==\"commit\" (exit /b 0)",
                    "if \"%1\"==\"pull\" (exit /b 0)",
                    "if \"%1\"==\"push\" (exit /b 0)",
                    "if \"%1\"==\"fetch\" (exit /b 0)",
                    "if \"%1\"==\"status\" goto Status",
                    "if \"%1\"==\"add\" goto Add",
                    "if \"%1\"==\"reset\" (exit /b 0)",
                    "if \"%1\"==\"diff\" goto Diff",
                    "if \"%1 %2\"==\"rev-parse HEAD\" (echo abc123& exit /b 0)",
                    "if \"%1 %2\"==\"rev-parse origin/master\" (echo abc123& exit /b 0)",
                    "exit /b 0",
                    ":Status",
                    "if \"%2\"==\"--short\" if \"%3\"==\"--branch\" (echo ## master...origin/master& echo ?? new_file.py& exit /b 0)",
                    "if \"%2\"==\"--short\" if \"%3\"==\"--untracked-files=all\" (echo ?? new_file.py& exit /b 0)",
                    "exit /b 0",
                    ":Add",
                    "if \"%2\"==\"-A\" if \"%3\"==\"--\" (exit /b 0)",
                    "exit /b 0",
                    ":Diff",
                    "if \"%2\"==\"--cached\" if \"%3\"==\"--stat\" (echo 2 files changed, 1 insertion^(+^), 1 deletion^(-^)& exit /b 0)",
                    "if \"%2\"==\"--cached\" if \"%3\"==\"--quiet\" (exit /b 1)",
                    "exit /b 0",
                ]
            ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATCH_DESKTOP_PUSH_REPO"] = str(repo_dir)
    env["PATCH_DESKTOP_PUSH_LOG"] = str(latest_log)
    env["FAKE_GIT_LOG"] = str(fake_git_log)
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["PATCH_DESKTOP_PUSH_SUPPRESS_CONSOLE_TITLE"] = "1"
    env["PATCH_DESKTOP_PUSH_NONINTERACTIVE"] = "1"

    result = _run_patch_desktop_push(script, gpt_dir, env)

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = latest_log.read_text(encoding="utf-8")
    assert "old log should be overwritten" not in log_text
    assert str(latest_log) in result.stdout
    assert "Status before staging, including untracked files:" in log_text
    assert "?? new_file.py" in log_text
    assert "Staged diff summary:" in log_text
    assert "Push verified: local HEAD matches origin/master." in log_text
    assert "Non-interactive mode: closing without pause." in result.stdout

    git_calls = fake_git_log.read_text(encoding="utf-8")
    assert "status --short --branch" in git_calls
    assert "status --short --untracked-files=all" in git_calls
    assert "add -A -- ." in git_calls
    assert 'reset -q -- "render/data"' in git_calls
    assert 'reset -q -- "state_backup.json"' in git_calls
    assert "diff --cached --stat" in git_calls
    assert "fetch origin master" in git_calls


def test_patch_desktop_push_pushes_existing_commit_when_nothing_new_is_staged(tmp_path: Path):
    script = ROOT / "PATCH_DESKTOP_PUSH.bat"
    gpt_dir = tmp_path / "GPT"
    repo_dir = gpt_dir / "CODEX-master"
    fake_bin = tmp_path / "bin"
    fake_git_log = tmp_path / "git-args.txt"
    latest_log = gpt_dir / "PATCH_DESKTOP_PUSH-latest.log"
    repo_dir.mkdir(parents=True)
    fake_bin.mkdir()
    (repo_dir / ".git").mkdir()

    fake_git = fake_bin / "git.bat"
    fake_git.write_text(
        "\n".join(
            [
                "@echo off",
                "echo %*>>\"%FAKE_GIT_LOG%\"",
                "if \"%1\"==\"--version\" (echo git version 2.0.0& exit /b 0)",
                "if \"%1\"==\"commit\" (exit /b 0)",
                "if \"%1\"==\"pull\" (exit /b 0)",
                "if \"%1\"==\"push\" (exit /b 0)",
                "if \"%1\"==\"fetch\" (exit /b 0)",
                "if \"%1\"==\"status\" goto Status",
                "if \"%1\"==\"add\" goto Add",
                "if \"%1\"==\"reset\" (exit /b 0)",
                "if \"%1\"==\"diff\" goto Diff",
                "if \"%1 %2\"==\"rev-parse HEAD\" (echo abc123& exit /b 0)",
                "if \"%1 %2\"==\"rev-parse origin/master\" (echo abc123& exit /b 0)",
                "exit /b 0",
                ":Status",
                "if \"%2\"==\"--short\" if \"%3\"==\"--branch\" (echo ## master...origin/master [ahead 1]& echo  M render/data/monthly_aud_revaluation_state.json& echo  M state_backup.json& exit /b 0)",
                "if \"%2\"==\"--short\" if \"%3\"==\"--untracked-files=all\" (echo  M render/data/monthly_aud_revaluation_state.json& echo  M state_backup.json& exit /b 0)",
                "exit /b 0",
                ":Add",
                "if \"%2\"==\"-A\" if \"%3\"==\"--\" (exit /b 0)",
                "exit /b 0",
                ":Diff",
                "if \"%2\"==\"--cached\" if \"%3\"==\"--stat\" (exit /b 0)",
                "if \"%2\"==\"--cached\" if \"%3\"==\"--quiet\" (exit /b 0)",
                "exit /b 0",
            ]
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATCH_DESKTOP_PUSH_REPO"] = str(repo_dir)
    env["PATCH_DESKTOP_PUSH_LOG"] = str(latest_log)
    env["FAKE_GIT_LOG"] = str(fake_git_log)
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["PATCH_DESKTOP_PUSH_SUPPRESS_CONSOLE_TITLE"] = "1"
    env["PATCH_DESKTOP_PUSH_NONINTERACTIVE"] = "1"

    result = _run_patch_desktop_push(script, gpt_dir, env)

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = latest_log.read_text(encoding="utf-8")
    assert "Nothing staged after git add -A -- . No new commit will be created." in log_text
    assert "Push verified: local HEAD matches origin/master." in log_text
    assert "Non-interactive mode: closing without pause." in result.stdout

    git_calls = fake_git_log.read_text(encoding="utf-8").splitlines()
    assert "push origin HEAD:master" in git_calls
    assert "fetch origin master" in git_calls
    assert not any(call.startswith("commit ") for call in git_calls)


def test_gitignore_excludes_generated_logs_caches_and_local_env_files():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    required = {
        ".pytest_cache/",
        ".pytest_tmp*/",
        ".pytest_tmp*",
        ".pytest_*.log",
        "__pycache__/",
        "*.pyc",
        "PATCH_DESKTOP_PUSH-latest.log",
        "INSTALL-latest.log",
        ".env",
        ".env.*",
        "*.env",
        "render/data/",
        "bybit_monitor/runtime_status.json",
        "bybit_monitor/state.json",
        "oanda_monitor/runtime_status.json",
        "state_backup.json",
    }
    assert required.issubset(set(ignore))
