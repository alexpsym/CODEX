"""Run pylint and pytest with tqdm progress bars and write logs."""
import os
import subprocess
import sys
from pathlib import Path
from contextlib import redirect_stdout

from tqdm import tqdm
import pytest
import core

LOG_DIR = core.LOG_DIR

PY_FILES = [
    "core.py",
    "scan.py",
    "continuous_scan.py",
    "test.py",
    "volume_math.py",
    "correlation_math.py",
]


def run_pylint() -> None:
    """Run pylint on all target files with a progress bar and log output."""
    for path in PY_FILES:
        if not Path(path).is_file():
            raise FileNotFoundError(f"Missing required file: {path}")

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "pylint.log")

    with open(log_path, "w", encoding="utf-8") as log_file:
        with tqdm(total=len(PY_FILES), desc="pylint") as pbar:
            for path in PY_FILES:
                result = subprocess.run([
                    "pylint",
                    path,
                ], text=True, capture_output=True)
                sys.stdout.write(result.stdout)
                log_file.write(result.stdout)
                sys.stdout.flush()
                log_file.flush()
                if result.returncode != 0:
                    raise SystemExit(f"pylint failed for {path}")
                pbar.update(1)


def run_pytest() -> None:
    """Run pytest with a tqdm progress bar for tests and log output."""

    class TqdmPlugin:  # pylint: disable=too-few-public-methods
        """pytest plugin showing progress with tqdm."""

        def __init__(self) -> None:
            self.pbar = None

        def pytest_collection_modifyitems(self, session, config, items):  # noqa: ARG002
            self.pbar = tqdm(total=len(items), desc="pytest")

        def pytest_runtest_logreport(self, report):
            if report.when == "call" and self.pbar:
                self.pbar.update(1)

        def pytest_sessionfinish(self, session, exitstatus):  # noqa: ARG002
            if self.pbar:
                self.pbar.close()

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "pytest.log")

    class Tee:
        """Simple class to duplicate writes to multiple streams."""

        def __init__(self, *streams) -> None:
            self.streams = streams

        def isatty(self) -> bool:  # pragma: no cover - passthrough
            return any(getattr(s, "isatty", lambda: False)() for s in self.streams)

        def write(self, data: str) -> None:
            for stream in self.streams:
                stream.write(data)

        def flush(self) -> None:
            for stream in self.streams:
                stream.flush()

    with open(log_path, "w", encoding="utf-8") as log_file:
        tee = Tee(sys.stdout, log_file)
        with redirect_stdout(tee):
            errno = pytest.main(["test.py"], plugins=[TqdmPlugin()])
    if errno != 0:
        raise SystemExit(errno)


def main() -> None:
    """Execute pylint and pytest with progress bars."""
    run_pylint()
    run_pytest()


if __name__ == "__main__":
    main()
