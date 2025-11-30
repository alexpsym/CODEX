import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
from getpass import getpass
from typing import List, Tuple
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback installation
    print("tqdm is required. Attempting to install tqdm...", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm"], stdout=sys.stdout)
    from tqdm import tqdm


GITHUB_API_BASE = "https://api.github.com/"
TOKEN_FILENAME = ".github_token"


def _token_file_path() -> str:
    script_dir = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(script_dir, TOKEN_FILENAME)


def _read_token_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as token_file:
            token = token_file.read().strip()
            if token:
                tqdm.write(f"Using cached token from {path}.")
            return token
    except FileNotFoundError:
        return ""


def _write_token_file(path: str, token: str) -> None:
    with open(path, "w", encoding="utf-8") as token_file:
        token_file.write(token.strip())

    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except (AttributeError, OSError):
        # Windows may not honor chmod in the same way; ignore failures.
        pass


def prompt_for_token() -> str:
    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token:
        tqdm.write("Using GitHub token from environment variable GITHUB_TOKEN.")
        return env_token.strip()

    token_path = _token_file_path()
    cached_token = _read_token_file(token_path)
    if cached_token:
        return cached_token

    print("A GitHub personal access token with repo scope is required.")
    token = getpass("Enter your GitHub token (input hidden): ").strip()
    if not token:
        raise RuntimeError("GitHub token is required to continue.")

    save_choice = input("Save this token for future runs? [Y/n]: ").strip().lower()
    if save_choice in {"", "y", "yes"}:
        _write_token_file(token_path, token)
        print(f"Token saved to {token_path}. Keep this file secure.")

    return token


def github_request(url: str, token: str):
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    request = Request(url, headers=headers)
    try:
        with urlopen(request) as response:
            payload = response.read().decode("utf-8")
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining is not None:
                print(f"GitHub API remaining requests: {remaining}")
            return json.loads(payload)
    except HTTPError as exc:
        if exc.code == 401:
            token_path = _token_file_path()
            raise RuntimeError(
                "GitHub API returned 401 Unauthorized. Provide a valid token via the "
                "GITHUB_TOKEN environment variable or update the cached token at "
                f"{token_path}."
            ) from exc
        raise


def fetch_repositories(token: str) -> List[dict]:
    repos: List[dict] = []
    page = 1
    with tqdm(desc="Fetching repositories", unit="page") as progress:
        while True:
            url = urljoin(GITHUB_API_BASE, f"user/repos?per_page=100&page={page}")
            data = github_request(url, token)
            if not data:
                break
            repos.extend(data)
            progress.update(1)
            page += 1
    return repos


def select_repository(repos: List[dict]) -> Tuple[str, str]:
    if not repos:
        raise RuntimeError("No repositories found for your account.")

    print("\nAvailable repositories:")
    for idx, repo in enumerate(repos, start=1):
        print(f"{idx:3d}. {repo['full_name']} (default branch: {repo.get('default_branch', 'main')})")

    while True:
        choice = input("\nEnter the number of the repository to push to: ").strip()
        if not choice.isdigit():
            print("Please enter a valid number.")
            continue
        selection = int(choice)
        if 1 <= selection <= len(repos):
            selected_repo = repos[selection - 1]
            return selected_repo["clone_url"], selected_repo.get("default_branch", "main")
        print("Selection out of range. Try again.")


def run_git_command(args: List[str], cwd: str) -> str:
    """Run a git command and raise when it fails."""

    result = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stdout}")
    if result.stdout:
        print(result.stdout)
    return result.stdout


def remote_branch_exists(remote_url: str, branch: str) -> bool:
    """Return True when origin/<branch> exists on the remote."""

    probe = subprocess.run(
        ["git", "ls-remote", "--heads", remote_url, branch],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if probe.returncode != 0:
        raise RuntimeError(f"git ls-remote failed while checking for remote branch:\n{probe.stdout}")
    if probe.stdout.strip():
        tqdm.write("Remote branch detected. Existing repository files will be preserved while applying your PUSH contents.")
        return True
    tqdm.write("Remote branch not found. A new branch will be created on push.")
    return False


def overlay_directory(src_dir: str, dst_dir: str) -> None:
    """Copy contents from src_dir into dst_dir without removing existing files."""

    for root, dirs, files in os.walk(src_dir):
        if ".git" in dirs:
            dirs.remove(".git")
        rel_root = os.path.relpath(root, src_dir)
        if rel_root == ".":
            rel_root = ""
        destination_root = os.path.join(dst_dir, rel_root)
        os.makedirs(destination_root, exist_ok=True)
        for file_name in files:
            if file_name == ".git":
                continue
            source_file = os.path.join(root, file_name)
            destination_file = os.path.join(destination_root, file_name)
            shutil.copy2(source_file, destination_file)


def working_tree_has_changes(repo_dir: str) -> bool:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if status.returncode != 0:
        raise RuntimeError(f"git status failed while checking for changes:\n{status.stdout}")
    return bool(status.stdout.strip())


def push_directory(push_dir: str, remote_url: str, branch: str) -> None:
    with tempfile.TemporaryDirectory(prefix="push_work_") as work_dir:
        repo_dir = os.path.join(work_dir, "repo")
        os.makedirs(repo_dir, exist_ok=True)

        with tqdm(total=7, desc="Preparing repository", unit="step") as progress:
            tqdm.write("Initializing isolated git working tree...")
            run_git_command(["init"], cwd=repo_dir)
            run_git_command(["remote", "add", "origin", remote_url], cwd=repo_dir)
            progress.update(1)

            tqdm.write("Checking remote branch state...")
            branch_exists = remote_branch_exists(remote_url, branch)
            progress.update(1)

            if branch_exists:
                tqdm.write("Fetching remote branch history...")
                run_git_command(["fetch", "origin", branch], cwd=repo_dir)
                run_git_command(["checkout", "-b", branch, f"origin/{branch}"], cwd=repo_dir)
                run_git_command(["reset", "--hard", f"origin/{branch}"], cwd=repo_dir)
            else:
                tqdm.write("Creating orphan branch for initial push...")
                run_git_command(["checkout", "--orphan", branch], cwd=repo_dir)
            progress.update(1)

            tqdm.write("Overlaying PUSH contents into working tree...")
            overlay_directory(push_dir, repo_dir)
            progress.update(1)

            tqdm.write("Staging files...")
            run_git_command(["add", "-A"], cwd=repo_dir)
            progress.update(1)

            if working_tree_has_changes(repo_dir):
                tqdm.write("Creating commit...")
                run_git_command(["commit", "-m", "Automated push"], cwd=repo_dir)
            else:
                tqdm.write("No changes detected; skipping commit.")
            progress.update(1)

            tqdm.write("Pushing to remote...")
            run_git_command(["push", "--set-upstream", "origin", branch], cwd=repo_dir)
            progress.update(1)



def main() -> None:
    try:
        token = prompt_for_token()
        repos = fetch_repositories(token)
        remote_url, branch = select_repository(repos)

        script_dir = os.path.abspath(os.path.dirname(__file__))
        push_dir = os.path.join(script_dir, "PUSH")
        if not os.path.isdir(push_dir):
            raise RuntimeError(f"PUSH directory not found at {push_dir}. Create it and add files before running.")

        push_directory(push_dir, remote_url, branch)
        print("\nPush completed successfully.")
    except Exception as exc:  # pylint: disable=broad-except
        print("\nAn error occurred:")
        print(str(exc))
        traceback.print_exc()
    finally:
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
