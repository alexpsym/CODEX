"""Durable, explicitly registered linear entry expiry. No application startup side effects."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = ROOT / "render/data/bybit_order_expiry.sqlite3"
ACTIVE = ("reserved", "scheduled", "reconciling", "submission_unresolved", "order_missing", "cancel_requested")
TERMINAL = ("filled", "cancelled", "partially_filled_cancelled", "rejected", "needs_attention")


class ExpiryError(ValueError):
    pass


class LostClaim(ExpiryError):
    pass


class ScopeMismatch(ExpiryError):
    pass


class BrokerError(RuntimeError):
    """Messages deliberately exclude broker bodies, credentials and headers."""


def utc_ms():
    return time.time_ns() // 1_000_000


def positive(value, *, zero=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (not zero and result == 0):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ExpiryError("Invalid order quantity") from None


@dataclass(frozen=True)
class ExecutionScope:
    mode: str
    environment: str
    profile: str
    fingerprint: str

    def __post_init__(self):
        url = urlsplit(self.environment)
        if (self.mode not in {"demo", "live"} or url.scheme != "https" or not url.hostname
                or url.username or url.password or url.path or url.query or url.fragment
                or self.profile not in {"KEY1", "KEY2", "DEMO_EXPLICIT", "LEGACY"}
                or not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint)):
            raise ExpiryError("Invalid explicit execution scope")

    @classmethod
    def from_record(cls, row):
        return cls(row["mode"], row["environment"], row["profile"], row["fingerprint"])


def credential_scope(mode, environment, key, secret, profile):
    if not key or not secret:
        raise ScopeMismatch("Credentials unavailable for the registered profile")
    base = str(environment).rstrip("/")
    fingerprint = hashlib.sha256(json.dumps(["ltt-expiry-v1", mode, base, profile, key, secret]).encode()).hexdigest()
    return ExecutionScope(mode, base, profile, fingerprint)


class WorkerLock:
    """Lifetime OS lock, independent of PID files and released on process death."""
    def __init__(self, queue_path):
        self.path = Path(queue_path).resolve()
        self.handle = None

    def acquire(self):
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            name = "Local\\LTTBybitExpiry_" + hashlib.sha256(str(self.path).casefold().encode()).hexdigest()
            handle = kernel.CreateMutexW(None, False, name)
            if not handle:
                raise OSError("Cannot establish expiry worker ownership")
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 0x80):
                kernel.CloseHandle(handle)
                return False
            self.handle = (kernel, handle)
        else:
            import fcntl
            handle = open(str(self.path) + ".worker.lock", "a+b")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                return False
            self.handle = handle
        return True

    def close(self):
        if self.handle is not None:
            if os.name == "nt":
                kernel, handle = self.handle
                kernel.ReleaseMutex(handle)
                kernel.CloseHandle(handle)
            else:
                self.handle.close()
            self.handle = None


class ExpiryQueue:
    def __init__(self, path=DEFAULT_QUEUE, *, clock=utc_ms):
        self.path, self.clock = Path(path).resolve(), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schedules(
                    id TEXT PRIMARY KEY, mode TEXT NOT NULL, environment TEXT NOT NULL,
                    profile TEXT NOT NULL, fingerprint TEXT NOT NULL, category TEXT NOT NULL CHECK(category='linear'),
                    symbol TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL, position_idx INTEGER NOT NULL,
                    order_link_id TEXT NOT NULL, order_id TEXT, purpose TEXT NOT NULL CHECK(purpose='ltt_limit_entry'),
                    deadline_ms INTEGER NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    reconciliations INTEGER NOT NULL DEFAULT 0, next_check_ms INTEGER NOT NULL,
                    lease_owner TEXT, lease_until_ms INTEGER, revision INTEGER NOT NULL DEFAULT 0,
                    filled_qty TEXT NOT NULL DEFAULT '0', leaves_qty TEXT, broker_status TEXT,
                    cancel_ack INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
                    created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
                    UNIQUE(mode,environment,fingerprint,order_link_id));
                CREATE UNIQUE INDEX IF NOT EXISTS exact_bound_order ON schedules(mode,environment,fingerprint,order_id)
                    WHERE order_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY, schedule_id TEXT NOT NULL,
                    at_ms INTEGER NOT NULL, event TEXT NOT NULL, details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS worker(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    instance TEXT NOT NULL, pid INTEGER NOT NULL, started_ms INTEGER NOT NULL,
                    heartbeat_ms INTEGER NOT NULL, stop_instance TEXT, stopped_ms INTEGER,
                    log_path TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '');
            """)

    @contextmanager
    def connection(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _event(self, db, identity, event, details):
        db.execute("INSERT INTO evidence(schedule_id,at_ms,event,details) VALUES(?,?,?,?)",
                   (identity, self.clock(), event, json.dumps(details, sort_keys=True)))

    def get(self, identity):
        with self.connection() as db:
            row = db.execute("SELECT * FROM schedules WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ExpiryError("Unknown schedule")
        return dict(row)

    def reserve(self, *, scope, symbol, side, quantity, deadline_ms, position_idx=0, order_link_id=None, require_ready=True):
        if not isinstance(scope, ExecutionScope) or not re.fullmatch(r"[A-Z0-9]+USDT", symbol or ""):
            raise ExpiryError("Only explicit linear USDT entry schedules are supported")
        if side not in {"Buy", "Sell"} or type(position_idx) is not int or position_idx not in (0, 1, 2):
            raise ExpiryError("Invalid entry side/position scope")
        if position_idx not in (0, 1 if side == "Buy" else 2):
            raise ExpiryError("Entry side conflicts with position scope")
        if type(deadline_ms) is not int or deadline_ms <= self.clock():
            raise ExpiryError("Deadline must be a future UTC epoch millisecond integer")
        quantity = str(positive(quantity))
        link = order_link_id or ("lttx_" + uuid4().hex[:30])
        if not re.fullmatch(r"lttx_[A-Za-z0-9_-]{8,31}", link):
            raise ExpiryError("A preassigned unique LTT expiry orderLinkId (max 36 characters) is required")
        if require_ready and not self.worker_status()["healthy"]:
            raise ExpiryError("Independent expiry worker is not ready; do not submit the order")
        with self.connection(write=True) as db:
            if require_ready:
                worker = db.execute("SELECT * FROM worker WHERE singleton=1").fetchone()
                if not worker or worker["stopped_ms"] or worker["stop_instance"] or self.clock() - worker["heartbeat_ms"] > 20_000:
                    raise ExpiryError("Worker readiness changed; do not submit the order")
            existing = db.execute("SELECT * FROM schedules WHERE mode=? AND environment=? AND fingerprint=? AND order_link_id=?",
                                  (scope.mode, scope.environment, scope.fingerprint, link)).fetchone()
            if existing:
                if any(existing[key] != value for key, value in {"symbol": symbol, "side": side, "quantity": quantity,
                        "deadline_ms": deadline_ms, "position_idx": position_idx, "profile": scope.profile}.items()):
                    raise ExpiryError("Conflicting registration for this exact intent")
                return dict(existing)
            identity, now = uuid4().hex, self.clock()
            db.execute("""INSERT INTO schedules(id,mode,environment,profile,fingerprint,category,symbol,side,quantity,
                position_idx,order_link_id,purpose,deadline_ms,state,next_check_ms,created_ms,updated_ms)
                VALUES(?,?,?,?,?,'linear',?,?,?,?,?,'ltt_limit_entry',?,'reserved',?,?,?)""",
                (identity, scope.mode, scope.environment, scope.profile, scope.fingerprint, symbol, side, quantity,
                 position_idx, link, deadline_ms, deadline_ms, now, now))
            self._event(db, identity, "reserved", {"deadline_ms": deadline_ms, "order_link_id": link})
        return self.get(identity)

    def bind(self, identity, *, scope, order_id, order_link_id):
        if not isinstance(order_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", order_id):
            raise ExpiryError("Missing or invalid accepted orderId")
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM schedules WHERE id=?", (identity,)).fetchone()
            if not row or ExecutionScope.from_record(row) != scope or row["order_link_id"] != order_link_id:
                raise ScopeMismatch("Binding scope/intent does not match")
            if row["order_id"] and row["order_id"] != order_id:
                raise ExpiryError("Conflicting accepted orderId")
            if not row["order_id"]:
                try:
                    db.execute("""UPDATE schedules SET order_id=?,state=CASE WHEN state='reserved' THEN 'scheduled' ELSE state END,
                        revision=revision+1,updated_ms=? WHERE id=?""", (order_id, self.clock(), identity))
                except sqlite3.IntegrityError:
                    raise ExpiryError("Accepted orderId already belongs to another intent") from None
                self._event(db, identity, "bound", {"order_id": order_id})
        return self.get(identity)

    def list_records(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM schedules ORDER BY created_ms DESC,id")]

    def reject_submission(self, identity, *, scope, order_link_id):
        """Only a definitive create rejection may retire an unbound reservation."""
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM schedules WHERE id=?", (identity,)).fetchone()
            if (not row or ExecutionScope.from_record(row) != scope or row["order_link_id"] != order_link_id
                    or row["order_id"] or row["attempts"] or row["lease_owner"] or row["state"] != "reserved"):
                raise ExpiryError("Submission rejection conflicts with newer reconciliation evidence")
            db.execute("UPDATE schedules SET state='rejected',broker_status='Rejected',updated_ms=?,revision=revision+1 WHERE id=?",
                       (self.clock(), identity))
            self._event(db, identity, "submission_rejected", {"order_link_id": order_link_id})
        return self.get(identity)

    def claim(self):
        now, owner = self.clock(), uuid4().hex
        with self.connection(write=True) as db:
            placeholders = ",".join("?" for _ in ACTIVE)
            row = db.execute(f"""SELECT * FROM schedules WHERE state IN ({placeholders}) AND deadline_ms<=?
                AND next_check_ms<=? AND (lease_owner IS NULL OR lease_until_ms<=?) ORDER BY deadline_ms,id LIMIT 1""",
                (*ACTIVE, now, now, now)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE schedules SET lease_owner=?,lease_until_ms=?,revision=revision+1 WHERE id=?",
                       (owner, now + 30_000, row["id"]))
            return dict(db.execute("SELECT * FROM schedules WHERE id=?", (row["id"],)).fetchone())

    def _claimed(self, db, row):
        current = db.execute("SELECT * FROM schedules WHERE id=?", (row["id"],)).fetchone()
        if not current or current["lease_owner"] != row["lease_owner"] or current["lease_until_ms"] < self.clock():
            raise LostClaim("Schedule claim expired or was replaced")
        return current

    def begin_cancel(self, row, evidence):
        with self.connection(write=True) as db:
            current = self._claimed(db, row)
            if current["attempts"] >= 3 or current["state"] not in ACTIVE:
                raise ExpiryError("Cancellation submissions exhausted")
            db.execute("""UPDATE schedules SET attempts=attempts+1,state='cancel_requested',cancel_ack=0,
                broker_status=?,filled_qty=?,leaves_qty=?,updated_ms=?,revision=revision+1 WHERE id=?""",
                (evidence["status"], evidence["filled"], evidence["leaves"], self.clock(), row["id"]))
            self._event(db, row["id"], "cancel_submission_started", {"attempt": current["attempts"] + 1, **evidence})

    def finish(self, row, state, *, error="", evidence=None, ack=None):
        if state not in ACTIVE + TERMINAL:
            raise ExpiryError("Unsupported schedule transition")
        with self.connection(write=True) as db:
            current = self._claimed(db, row)
            evidence = evidence or {"status": current["broker_status"], "filled": current["filled_qty"], "leaves": current["leaves_qty"]}
            checks = current["reconciliations"] + 1
            wait = min(60_000, 5_000 * 2 ** min(checks - 1, 4)) if error else 5_000
            db.execute("""UPDATE schedules SET state=?,last_error=?,broker_status=?,filled_qty=?,leaves_qty=?,
                cancel_ack=?,reconciliations=?,next_check_ms=?,lease_owner=NULL,lease_until_ms=NULL,
                updated_ms=?,revision=revision+1 WHERE id=?""",
                (state, error, evidence["status"], evidence["filled"], evidence["leaves"],
                 current["cancel_ack"] if ack is None else int(ack), checks, self.clock() + wait, self.clock(), row["id"]))
            if (state, error, evidence["status"], evidence["filled"]) != (current["state"], current["last_error"], current["broker_status"], current["filled_qty"]) or ack is not None:
                self._event(db, row["id"], state, {"error": error, **evidence})
        return self.get(row["id"])

    def worker_status(self):
        with self.connection() as db:
            worker = db.execute("SELECT * FROM worker WHERE singleton=1").fetchone()
            counts = dict(db.execute("SELECT state,count(*) FROM schedules GROUP BY state").fetchall())
            overdue = db.execute("SELECT count(*) FROM schedules WHERE deadline_ms<=? AND state NOT IN ('filled','cancelled','partially_filled_cancelled','rejected')", (self.clock(),)).fetchone()[0]
            latest = db.execute("SELECT last_error FROM schedules WHERE last_error!='' ORDER BY updated_ms DESC LIMIT 1").fetchone()
        lock = WorkerLock(self.path)
        free = lock.acquire()
        if free:
            lock.close()
        data = dict(worker) if worker else {}
        healthy = bool(worker and not free and not worker["stopped_ms"] and not worker["stop_instance"]
                       and 0 <= self.clock() - worker["heartbeat_ms"] <= 20_000)
        return {"healthy": healthy, "ownership_locked": not free, "worker": data,
                "pending": sum(counts.get(state, 0) for state in ACTIVE), "overdue": overdue,
                "needs_attention": counts.get("needs_attention", 0), "states": counts,
                "last_error": data.get("last_error") or (latest[0] if latest else "")}

    def worker_begin(self, instance, log_path):
        now = self.clock()
        with self.connection(write=True) as db:
            db.execute("INSERT OR REPLACE INTO worker VALUES(1,?,?,?,?,NULL,NULL,?,'')",
                       (instance, os.getpid(), now, now, str(log_path)))

    def heartbeat(self, instance, *, error="", stopped=False):
        with self.connection(write=True) as db:
            db.execute("UPDATE worker SET heartbeat_ms=?,last_error=?,stopped_ms=? WHERE singleton=1 AND instance=?",
                       (self.clock(), error, self.clock() if stopped else None, instance))
            row = db.execute("SELECT * FROM worker WHERE singleton=1").fetchone()
            return not row or row["instance"] != instance or row["stop_instance"] == instance

    def request_stop(self, instance):
        status = self.worker_status()
        if not status["healthy"] or status["worker"].get("instance") != instance:
            raise ExpiryError("No verified healthy expiry instance matches this stop request")
        with self.connection(write=True) as db:
            changed = db.execute("UPDATE worker SET stop_instance=? WHERE singleton=1 AND instance=? AND stopped_ms IS NULL",
                                 (instance, instance)).rowcount
            if not changed:
                raise ExpiryError("Worker instance changed; stop was not sent")


class BybitTransport:
    """Only exact GET reconciliation and POST cancel are available; no write retries."""
    def __init__(self, scope):
        from bybit_credentials import resolve_bybit_credentials_for
        import httpx
        mode, key, secret, base, profile = resolve_bybit_credentials_for(scope.mode)
        current = credential_scope(mode, base, key, secret, profile)
        if current != scope:
            raise ScopeMismatch("Configured credentials/environment differ from the registered scope")
        self.scope, self.key, self.secret = current, key, secret
        self.client = httpx.Client(timeout=5., follow_redirects=False)

    def _request(self, method, path, params):
        allowed = {("GET", "/v5/order/realtime"), ("GET", "/v5/order/history"), ("POST", "/v5/order/cancel")}
        if (method, path) not in allowed or params.get("category") != "linear" or not (params.get("orderId") or params.get("orderLinkId")):
            raise ExpiryError("Only exact registered-order operations are allowed")
        stamp, window = str(utc_ms()), "5000"
        data = urlencode(sorted(params.items())) if method == "GET" else json.dumps(params, separators=(",", ":"))
        signature = hmac.new(self.secret.encode(), (stamp + self.key + window + data).encode(), hashlib.sha256).hexdigest()
        headers = {"X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": stamp, "X-BAPI-RECV-WINDOW": window,
                   "X-BAPI-SIGN": signature, "X-BAPI-SIGN-TYPE": "2", "Content-Type": "application/json"}
        url = self.scope.environment + path
        response = self.client.request(method, url + ("?" + data if method == "GET" else ""),
                                       headers=headers, **({"content": data} if method == "POST" else {}))
        if response.status_code != 200:
            raise BrokerError("Bybit HTTP request failed")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("retCode") not in (0, "0") or not isinstance(payload.get("result"), dict):
            raise BrokerError("Bybit did not confirm the request")
        return payload["result"]

    def orders(self, schedule, *, history=False):
        params = {"category": "linear", "symbol": schedule["symbol"]}
        params["orderId" if schedule["order_id"] else "orderLinkId"] = schedule["order_id"] or schedule["order_link_id"]
        result = self._request("GET", "/v5/order/history" if history else "/v5/order/realtime", params)
        if result.get("category") != "linear" or not isinstance(result.get("list"), list) or result.get("nextPageCursor"):
            raise BrokerError("Ambiguous or malformed exact-order result")
        return result["list"]

    def cancel(self, schedule):
        return self._request("POST", "/v5/order/cancel", {"category": "linear", "symbol": schedule["symbol"], "orderId": schedule["order_id"]})

    def close(self):
        self.client.close()


def validate_entry(schedule, order):
    if (not isinstance(order, dict) or order.get("symbol") != schedule["symbol"] or order.get("side") != schedule["side"]
            or order.get("orderLinkId") != schedule["order_link_id"] or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(order.get("orderId") or ""))
            or (schedule["order_id"] and order.get("orderId") != schedule["order_id"])
            or order.get("orderType") != "Limit" or order.get("reduceOnly") is not False or order.get("closeOnTrigger") is not False
            or order.get("stopOrderType") not in {"", "UNKNOWN"} or str(order.get("triggerPrice")) not in {"", "0", "0.0"}
            or type(order.get("positionIdx")) is not int or order["positionIdx"] != schedule["position_idx"]):
        raise ExpiryError("Broker identity or limit-entry purpose does not match")
    try:
        if int(order["createdTime"]) < schedule["created_ms"] - 5_000:
            raise ExpiryError("Order predates the registered intent")
    except (KeyError, ValueError, TypeError):
        raise ExpiryError("Missing or mismatched order creation evidence") from None
    qty, filled, leaves = positive(order.get("qty")), positive(order.get("cumExecQty"), zero=True), positive(order.get("leavesQty"), zero=True)
    if qty != positive(schedule["quantity"]) or filled > qty or leaves > qty - filled or filled < positive(schedule["filled_qty"], zero=True):
        raise ExpiryError("Inconsistent or regressing fill evidence")
    status = order.get("orderStatus")
    if status not in {"New", "PartiallyFilled", "Filled", "Cancelled", "PartiallyFilledCanceled", "Rejected"}:
        raise ExpiryError("Unsupported order state; manual attention required")
    if ((status == "New" and (filled != 0 or leaves != qty))
            or (status == "PartiallyFilled" and not (0 < filled < qty and filled + leaves == qty))
            or (status == "Filled" and (filled != qty or leaves != 0))
            or (status == "Rejected" and filled != 0)):
        raise ExpiryError("Inconsistent broker state/quantity evidence")
    return {"status": status, "filled": str(filled), "leaves": str(leaves)}


class ExpiryEngine:
    def __init__(self, queue, transport_factory=BybitTransport):
        self.queue, self.transport_factory = queue, transport_factory

    def run_once(self):
        row = self.queue.claim()
        if row is None:
            return None
        transport = None
        try:
            scope = ExecutionScope.from_record(row)
            transport = self.transport_factory(scope)
            if transport.scope != scope:
                raise ScopeMismatch("Transport scope differs from registered profile")
            orders = transport.orders(row)
            if not orders:
                orders = transport.orders(row, history=True)
            if not orders:
                return self.queue.finish(row, "needs_attention" if row["attempts"] >= 3 else ("order_missing" if row["order_id"] else "submission_unresolved"),
                                         error="Exact order absent from realtime and history; cancellation is unconfirmed")
            if len(orders) != 1:
                raise ExpiryError("Ambiguous exact-order evidence")
            order = orders[0]
            evidence = validate_entry(row, order)
            if not row["order_id"]:
                self.queue.bind(row["id"], scope=scope, order_id=order["orderId"], order_link_id=row["order_link_id"])
                row["order_id"] = order["orderId"]
            status, filled = evidence["status"], positive(evidence["filled"], zero=True)
            terminal = {"Filled": "filled", "Rejected": "rejected", "Cancelled": "partially_filled_cancelled" if filled else "cancelled",
                        "PartiallyFilledCanceled": "partially_filled_cancelled" if filled else "cancelled"}
            if status in terminal:
                return self.queue.finish(row, terminal[status], evidence=evidence)
            if row["attempts"] >= 3:
                return self.queue.finish(row, "needs_attention", error="Three cancellation submissions exhausted; broker still reports an active entry", evidence=evidence)
            self.queue.begin_cancel(row, evidence)  # Durable BEFORE the potentially uncertain outbound operation.
            result = transport.cancel(row)
            if result.get("orderId") != row["order_id"] or result.get("orderLinkId") != row["order_link_id"]:
                return self.queue.finish(row, "reconciling", error="Cancellation acknowledgement identity unconfirmed", evidence=evidence)
            return self.queue.finish(row, "cancel_requested", evidence=evidence, ack=True)
        except LostClaim:
            return None
        except ExpiryError as exc:
            return self.queue.finish(row, "needs_attention", error=str(exc))
        except Exception:
            return self.queue.finish(row, "reconciling", error="Broker/API/network outcome unavailable; reconcile before any further cancellation")
        finally:
            if transport is not None:
                try:
                    transport.close()
                except Exception:
                    pass  # Closing a transport cannot replace durable reconciliation evidence.
