"""Independent local expiry executor. Calculator integration is intentionally separate."""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import os
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from shared.bybit_order_expiry import DEFAULT_QUEUE, ExpiryEngine, ExpiryError, ExpiryQueue, WorkerLock, diagnostic_message

DEFAULT_LOG = ROOT / "logs/bybit_order_expiry_worker.log"


def start_worker(queue_path=DEFAULT_QUEUE, *, log_path=DEFAULT_LOG, fixture_seconds=None):
    queue = ExpiryQueue(queue_path)
    status = queue.worker_status()
    if status["healthy"]:
        return status
    if status["ownership_locked"]:
        raise ExpiryError("Worker owns this queue but is not ready; inspect status/log before submitting")
    command = [sys.executable, str(Path(__file__).resolve()), "run", "--queue", str(queue.path), "--log", str(log_path)]
    if fixture_seconds is not None:
        command += ["--fixture-seconds", str(fixture_seconds)]
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "cwd": str(ROOT), "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    else:
        kwargs["start_new_session"] = True
    child = subprocess.Popen(command, **kwargs)
    until = time.monotonic() + 5
    while time.monotonic() < until:
        status = queue.worker_status()
        if status["healthy"]:
            return status
        if child.poll() is not None:
            # A competing start may have won the ownership lock.
            status = queue.worker_status()
            if status["healthy"]:
                return status
            raise ExpiryError("Detached worker failed readiness; inspect its log; do not submit")
        time.sleep(.1)
    raise ExpiryError("Worker readiness timed out; inspect status; do not submit")


def stop_worker(queue_path=DEFAULT_QUEUE, *, instance=None):
    queue = ExpiryQueue(queue_path)
    status = queue.worker_status()
    if not status["ownership_locked"]:
        return status
    target = instance or status["worker"].get("instance")
    queue.request_stop(target)
    # Cooperative nonce-addressed stop; never signal or terminate a PID.
    return {**queue.worker_status(), "stop_requested": True}


def run_worker(queue_path, log_path, *, fixture_seconds=None):
    queue = ExpiryQueue(queue_path)
    if fixture_seconds is not None:
        if (not 0 < fixture_seconds <= 20 or queue.path == DEFAULT_QUEUE.resolve()
                or queue.worker_status()["states"]):
            raise ExpiryError("Fixture mode requires a bounded empty non-production queue")
    lock = WorkerLock(queue.path)
    if not lock.acquire():
        return 0
    instance = uuid4().hex
    logger = logging.getLogger("bybit_order_expiry")
    logger.setLevel(logging.INFO)
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    try:
        if fixture_seconds is None:
            from shared.env_bootstrap import load_master_env
            load_master_env(base_dir=ROOT)
        queue.worker_begin(instance, Path(log_path).resolve())
        logger.info("Expiry worker started instance=%s", instance)
        engine = ExpiryEngine(queue)
        end = time.monotonic() + fixture_seconds if fixture_seconds is not None else float("inf")
        last_error = ""
        next_scan = 0
        last_schedule_state = {}
        while time.monotonic() < end:
            try:
                if time.monotonic() >= next_scan:
                    if queue.heartbeat(instance, error=last_error):
                        break
                    if fixture_seconds is None:
                        # Bound each scan; all broker requests are individually bounded.
                        scan_until = time.monotonic() + 5
                        for _ in range(20):
                            result = engine.run_once()
                            if result is not None:
                                useful = tuple(result.get(key) for key in ("state", "attempts", "broker_status", "filled_qty", "last_error_details"))
                                if last_schedule_state.get(result["id"]) != useful:
                                    try:
                                        detail = diagnostic_message(json.loads(result.get("last_error_details") or "{}"))
                                    except (ValueError, TypeError):
                                        detail = ""
                                    logger.info("Schedule=%s state=%s attempts=%s %s", result["id"], result["state"], result["attempts"], detail)
                                    last_schedule_state[result["id"]] = useful
                            if queue.heartbeat(instance) or result is None or time.monotonic() >= scan_until:
                                break
                    else:
                        with queue.connection() as db:
                            if db.execute("SELECT count(*) FROM schedules").fetchone()[0]:
                                raise ExpiryError("Fixture queue must remain empty")
                    last_error = ""
                    next_scan = time.monotonic() + 5
                with queue.connection() as db:
                    owner = db.execute("SELECT instance,stop_instance FROM worker WHERE singleton=1").fetchone()
                if not owner or owner["instance"] != instance or owner["stop_instance"] == instance:
                    break
            except Exception:
                error = "Queue/executor unavailable; inspect local storage and connectivity"
                if error != last_error:
                    logger.error(error)
                last_error = error
                next_scan = time.monotonic() + 5
            time.sleep(.2)
        queue.heartbeat(instance, error=last_error, stopped=True)
        logger.info("Expiry worker stopped instance=%s", instance)
        return 0
    finally:
        handler.close()
        logger.removeHandler(handler)
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "status", "stop", "run"))
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--instance", help="Optional expected worker nonce for stop")
    parser.add_argument("--fixture-seconds", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.command == "run":
            return run_worker(args.queue, args.log, fixture_seconds=args.fixture_seconds)
        if args.fixture_seconds is not None and args.command != "start":
            raise ExpiryError("Fixture duration is only valid for start/run")
        if args.command == "start":
            status = start_worker(args.queue, log_path=args.log, fixture_seconds=args.fixture_seconds)
        elif args.command == "stop":
            status = stop_worker(args.queue, instance=args.instance)
        else:
            status = ExpiryQueue(args.queue).worker_status()
        print(json.dumps(status, sort_keys=True))
        return 0
    except (ExpiryError, OSError):
        print("Expiry worker command failed; inspect queue permissions/status/log; do not submit an expiry-enabled order", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
