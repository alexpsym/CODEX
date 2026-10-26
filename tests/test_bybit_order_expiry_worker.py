from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from shared.bybit_order_expiry import (ExpiryQueue, ExpiryEngine, ExpiryError, LostClaim,
                                      credential_scope, BybitTransport)

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.now = 1_800_000_000_000

    def __call__(self):
        return self.now

    def advance(self, ms=60_000):
        self.now += ms


def scope(mode="demo", key="fixture-key"):
    return credential_scope(mode, "https://api-demo.bybit.com" if mode == "demo" else "https://api.bybit.com",
                            key, "fixture-secret", "DEMO_EXPLICIT" if mode == "demo" else "KEY1")


def reserve(queue, *, bind=True, **kwargs):
    row = queue.reserve(scope=kwargs.pop("scope", scope()), symbol="BTCUSDT", side="Buy", quantity="2",
                        deadline_ms=queue.clock() + 1000, require_ready=False, **kwargs)
    if bind:
        row = queue.bind(row["id"], scope=scope() if row["mode"] == "demo" else scope("live"),
                         order_id="order-" + row["id"], order_link_id=row["order_link_id"])
    return row


def order(row, **changes):
    result = dict(orderId=row["order_id"] or "recovered-order", orderLinkId=row["order_link_id"],
                  symbol=row["symbol"], side=row["side"], orderType="Limit", reduceOnly=False,
                  closeOnTrigger=False, stopOrderType="", triggerPrice="0", positionIdx=0,
                  createdTime=str(row["created_ms"]), qty="2", cumExecQty="0", leavesQty="2", orderStatus="New")
    result.update(changes)
    return result


class Broker:
    def __init__(self):
        self.current, self.history, self.calls = {}, {}, []
        self.fail_cancel = False
        self.fail_query = False

    def factory(self, execution_scope):
        parent = self

        class Transport:
            scope = execution_scope

            def orders(self, row, *, history=False):
                parent.calls.append(("history" if history else "query", row["id"], row["order_id"], row["order_link_id"]))
                if parent.fail_query:
                    raise TimeoutError("do not persist secret error text")
                return (parent.history if history else parent.current).get(row["id"], [])

            def cancel(self, row):
                parent.calls.append(("cancel", row["id"], row["order_id"], row["order_link_id"]))
                if parent.fail_cancel:
                    raise TimeoutError("ambiguous acknowledgement")
                return {"orderId": row["order_id"], "orderLinkId": row["order_link_id"]}

            def close(self):
                pass

        return Transport()


def test_expiry_queue_is_durable_scoped_and_transactional(tmp_path):
    clock = Clock()
    path = tmp_path / "queue.sqlite3"
    queue = ExpiryQueue(path, clock=clock)
    with pytest.raises(ExpiryError, match="not ready"):
        queue.reserve(scope=scope(), symbol="BTCUSDT", side="Buy", quantity="2", deadline_ms=clock()+1000)
    link = "lttx_concurrent_intent"

    def register(_):
        return ExpiryQueue(path, clock=clock).reserve(scope=scope(), symbol="BTCUSDT", side="Buy", quantity="2",
                    deadline_ms=clock()+1000, order_link_id=link, require_ready=False)["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(register, range(8)))
    assert len(set(identities)) == 1
    row = queue.get(identities[0])
    with pytest.raises(ExpiryError, match="Conflicting"):
        queue.reserve(scope=scope(), symbol="BTCUSDT", side="Sell", quantity="2", deadline_ms=clock()+1000,
                      order_link_id=link, require_ready=False)
    other = reserve(queue, scope=scope("live"), order_link_id=link)
    assert other["id"] != row["id"]
    with pytest.raises(ExpiryError, match="scope"):
        queue.bind(row["id"], scope=scope("live"), order_id="wrong", order_link_id=link)
    bound = queue.bind(row["id"], scope=scope(), order_id="accepted", order_link_id=link)
    assert queue.bind(row["id"], scope=scope(), order_id="accepted", order_link_id=link) == bound
    with pytest.raises(ExpiryError, match="Conflicting"):
        queue.bind(row["id"], scope=scope(), order_id="different", order_link_id=link)
    for bad in (True, 0, clock()-1):
        with pytest.raises(ExpiryError):
            queue.reserve(scope=scope(), symbol="BTCUSDT", side="Buy", quantity="2", deadline_ms=bad, require_ready=False)
    clock.advance()
    with ThreadPoolExecutor(max_workers=3) as pool:
        claims = list(pool.map(lambda _: ExpiryQueue(path, clock=clock).claim(), range(3)))
    assert len([r for r in claims if r]) == 2
    first = next(r for r in claims if r and r["id"] == row["id"])
    clock.advance(31_000)
    replacement = queue.claim()
    with pytest.raises(LostClaim):
        queue.finish(first, "cancelled")
    assert replacement["lease_owner"] != first["lease_owner"]
    reloaded = ExpiryQueue(path, clock=clock).get(row["id"])
    assert reloaded["order_id"] == "accepted" and reloaded["category"] == "linear"
    assert reloaded["fingerprint"] == scope().fingerprint
    assert "fixture-secret" not in path.read_bytes().decode("latin1")


def test_due_expiry_cancels_only_registered_unfilled_entry_remainder(tmp_path, monkeypatch):
    clock = Clock()
    queue = ExpiryQueue(tmp_path / "queue.sqlite3", clock=clock)
    broker = Broker()
    engine = ExpiryEngine(queue, broker.factory)
    new = reserve(queue)
    partial = reserve(queue)
    filled = reserve(queue)
    dangerous = reserve(queue)
    ambiguous = reserve(queue)
    broker.current = {new["id"]: [order(new)],
                      partial["id"]: [order(partial, orderStatus="PartiallyFilled", cumExecQty=".5", leavesQty="1.5")],
                      filled["id"]: [order(filled, orderStatus="Filled", cumExecQty="2", leavesQty="0")],
                      dangerous["id"]: [order(dangerous, reduceOnly=True)],
                      ambiguous["id"]: [order(ambiguous), order(ambiguous)]}
    assert engine.run_once() is None  # Future entries are untouched.
    clock.advance(1001)
    for _ in range(5):
        engine.run_once()
    cancels = [call for call in broker.calls if call[0] == "cancel"]
    assert {call[1] for call in cancels} == {new["id"], partial["id"]}
    assert all(call[2] for call in cancels)
    assert queue.get(new["id"])["state"] == "cancel_requested"
    assert queue.get(partial["id"])["filled_qty"] == "0.5"
    assert queue.get(filled["id"])["state"] == "filled"
    assert queue.get(dangerous["id"])["state"] == queue.get(ambiguous["id"])["state"] == "needs_attention"
    broker.current[partial["id"]] = []
    broker.history[partial["id"]] = [order(partial, orderStatus="Cancelled", cumExecQty=".5", leavesQty="0")]
    broker.current[new["id"]] = [order(new, orderStatus="Filled", cumExecQty="2", leavesQty="0")]
    clock.advance()
    engine.run_once(); engine.run_once()
    assert queue.get(partial["id"])["state"] == "partially_filled_cancelled"
    assert queue.get(new["id"])["state"] == "filled"  # Fill racing cancellation is not expiry success.
    assert len([c for c in broker.calls if c[0] == "cancel"]) == 2
    # Real transport wire contract with fake HTTP and explicit fake credential resolution.
    import bybit_credentials
    import httpx
    monkeypatch.setattr(bybit_credentials, "resolve_bybit_credentials_for", lambda mode: (mode, "fixture-key", "fixture-secret", "https://api-demo.bybit.com", "DEMO_EXPLICIT"))
    requests = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 5., "follow_redirects": False}
        def request(self, method, url, **kwargs):
            requests.append((method, url, kwargs))
            return httpx.Response(200, json={"retCode": 0, "result": {"category": "linear", "list": [], "orderId": new["order_id"], "orderLinkId": new["order_link_id"]}})
        def close(self): pass
    monkeypatch.setattr(httpx, "Client", Client)
    transport = BybitTransport(scope())
    transport.orders(new); transport.orders(new, history=True); transport.cancel(new)
    assert [r[0] for r in requests] == ["GET", "GET", "POST"]
    assert "/v5/order/history?" in requests[1][1]
    assert json.loads(requests[2][2]["content"]) == {"category": "linear", "symbol": "BTCUSDT", "orderId": new["order_id"]}
    with pytest.raises(ExpiryError): BybitTransport(scope(key="changed"))
    assert len(requests) == 3


def test_expiry_reconciles_uncertain_outcomes_and_recovers_after_restart(tmp_path):
    clock = Clock()
    path = tmp_path / "queue.sqlite3"
    queue = ExpiryQueue(path, clock=clock)
    broker = Broker()
    row = reserve(queue, bind=False)
    broker.current[row["id"]] = [order(row)]
    broker.fail_cancel = True
    clock.advance(1001)
    result = ExpiryEngine(queue, broker.factory).run_once()
    assert result["state"] == "reconciling" and result["attempts"] == 1
    assert result["order_id"] == "recovered-order"  # Lost create response recovered by exact link.
    queue = ExpiryQueue(path, clock=clock)
    assert ExpiryEngine(queue, broker.factory).run_once() is None
    broker.fail_query = True
    clock.advance()
    result = ExpiryEngine(queue, broker.factory).run_once()
    assert result["attempts"] == 1 and "secret" not in result["last_error"]
    broker.fail_query = False
    for expected in (2, 3):
        clock.advance()
        result = ExpiryEngine(ExpiryQueue(path, clock=clock), broker.factory).run_once()
        assert result["attempts"] == expected
        assert broker.calls[-2][0] == "query" and broker.calls[-1][0] == "cancel"
    clock.advance()
    result = ExpiryEngine(queue, broker.factory).run_once()
    assert result["state"] == "needs_attention" and result["attempts"] == 3
    assert 5000 <= result["next_check_ms"] - clock() <= 60_000
    missing = reserve(queue, bind=False)
    clock.advance()
    result = ExpiryEngine(queue, broker.factory).run_once()
    assert result["id"] == missing["id"] and result["state"] == "submission_unresolved"
    assert [c[0] for c in broker.calls if c[1] == missing["id"]] == ["query", "history"]
    # Crash after persisting a submission but before receiving its result: reclaim, query history, never blindly retry.
    crash = reserve(queue)
    clock.advance(1001)
    claimed = queue.claim()
    assert claimed["id"] == crash["id"]
    queue.begin_cancel(claimed, {"status": "New", "filled": "0", "leaves": "2"})
    clock.advance(31_000)
    broker.history[crash["id"]] = [order(crash, orderStatus="Cancelled", leavesQty="0")]
    # Missing earlier record can also be due; reconcile it first if selected.
    for _ in range(2):
        ExpiryEngine(ExpiryQueue(path, clock=clock), broker.factory).run_once()
    saved = queue.get(crash["id"])
    assert saved["state"] == "cancelled" and saved["attempts"] == 1
    assert not [c for c in broker.calls if c[0] == "cancel" and c[1] == crash["id"]]
    rejected = reserve(queue)
    broker.history[rejected["id"]] = [order(rejected, orderStatus="Rejected", leavesQty="0")]
    clock.advance(1001)
    assert ExpiryEngine(queue, broker.factory).run_once()["state"] == "rejected"


def test_expiry_worker_is_singleton_and_survives_parent_exit(tmp_path):
    from tools.bybit_order_expiry_worker import start_worker, stop_worker
    path, log = tmp_path / "empty.sqlite3", tmp_path / "worker.log"
    # This parent exits completely; its detached child is bounded to 18 seconds and cannot load credentials or process orders.
    script = "from tools.bybit_order_expiry_worker import start_worker; import json,sys; print(json.dumps(start_worker(sys.argv[1],log_path=sys.argv[2],fixture_seconds=18)))"
    env = dict(os.environ, MASTER_ENV_FILE=str(tmp_path / "no-env"), PYTHONPATH=str(ROOT))
    status = None
    try:
        parent = subprocess.run([sys.executable, "-c", script, str(path), str(log)], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=20)
        assert parent.returncode == 0, parent.stderr
        status = json.loads(parent.stdout)
        instance = status["worker"]["instance"]
        queue = ExpiryQueue(path)
        assert queue.worker_status()["healthy"]  # Parent is already gone.
        duplicate = start_worker(path, log_path=log, fixture_seconds=18)
        assert duplicate["worker"]["instance"] == instance
        assert duplicate["worker"]["pid"] == status["worker"]["pid"]
        with pytest.raises(ExpiryError): stop_worker(path, instance="wrong-instance")
        assert queue.worker_status()["healthy"]
        assert stop_worker(path, instance=instance)["stop_requested"]
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and queue.worker_status()["ownership_locked"]:
            time.sleep(.1)
        final = queue.worker_status()
        assert not final["healthy"] and not final["ownership_locked"]
        assert final["worker"]["stopped_ms"] and final["pending"] == 0
        assert "stopped" in log.read_text()
    finally:
        if status:
            current = ExpiryQueue(path).worker_status()
            if current["healthy"] and current["worker"]["instance"] == status["worker"]["instance"]:
                stop_worker(path, instance=status["worker"]["instance"])


def _repair_http(monkeypatch, responder):
    """Keep the real authenticated transport/parser; replace only HTTP and credentials."""
    import bybit_credentials
    import httpx
    monkeypatch.setattr(bybit_credentials, "resolve_bybit_credentials_for", lambda mode: (mode,"fixture-key","fixture-secret","https://api-demo.bybit.com","DEMO_EXPLICIT"))
    calls=[]
    class Client:
        def __init__(self, **kwargs):
            assert kwargs=={"timeout":5.,"follow_redirects":False}
        def request(self, method, url, **kwargs):
            from urllib.parse import urlsplit, parse_qs
            parsed=urlsplit(url)
            assert parsed.netloc=="api-demo.bybit.com"
            params=parse_qs(parsed.query) if method=="GET" else json.loads(kwargs["content"])
            calls.append((method,parsed.path,params))
            return responder(method,parsed.path,params)
        def close(self): pass
    monkeypatch.setattr(httpx,"Client",Client)
    return calls


def test_expiry_exact_order_cursor_and_terminal_blank_remainder(tmp_path,monkeypatch):
    import httpx
    clock=Clock();path=tmp_path/"expiry.sqlite3";queue=ExpiryQueue(path,clock=clock)
    row=reserve(queue);manual=reserve(queue)
    active={row["id"]:True,manual["id"]:False}
    def response(method,path,params):
        if method=="POST":
            assert path=="/v5/order/cancel" and params["orderId"]==row["order_id"]
            return httpx.Response(200,json={"retCode":0,"result":{"orderId":row["order_id"],"orderLinkId":row["order_link_id"]}})
        assert path=="/v5/order/realtime"
        selected=row if params["orderId"]==[row["order_id"]] else manual
        evidence=order(selected) if active[selected["id"]] else order(selected,orderStatus="Cancelled",leavesQty="")
        return httpx.Response(200,json={"retCode":0,"result":{"category":"linear","nextPageCursor":"opaque-single-result-cursor","list":[evidence]}})
    calls=_repair_http(monkeypatch,response)
    clock.advance(1001)
    engine=ExpiryEngine(queue)
    for _ in range(2): engine.run_once()
    saved=queue.get(row["id"]);assert saved["state"]=="cancel_requested" and saved["attempts"]==1 and saved["cancel_ack"]==1
    assert queue.get(manual["id"])["state"]=="cancelled" and queue.get(manual["id"])["attempts"]==0
    active[row["id"]]=False;clock.advance()
    assert engine.run_once()["state"]=="cancelled"
    assert len([c for c in calls if c[0]=="POST"])==1
    assert all(c[2].get("orderId") for c in calls)
    # Empty active quantities, duplicate/foreign records and empty cursor pages remain unsafe.
    unsafe=reserve(queue);clock.advance(1001)
    from shared.bybit_order_expiry import BrokerError, validate_entry
    for listing,cursor in (([order(unsafe,leavesQty="")],"x"),([order(unsafe),order(unsafe)],"x"),
                           ([order(unsafe,orderId="foreign")],"x"),([],"x"),([order(unsafe,reduceOnly=True)],"x")):
        def malformed(method,path,params):
            assert method=="GET"
            return httpx.Response(200,json={"retCode":0,"result":{"category":"linear","list":listing,"nextPageCursor":cursor}})
        bad_calls=_repair_http(monkeypatch,malformed)
        transport=BybitTransport(scope())
        try:
            with pytest.raises((ExpiryError,BrokerError)):
                observed=transport.orders(unsafe)
                validate_entry(unsafe,observed[0])
        finally:
            transport.close()
        assert len(bad_calls)==1 and bad_calls[0][0]=="GET"
    # Existing-schema upgrade preserves the registered deadline and attempt evidence.
    before=queue.get(row["id"])
    with queue.connection(write=True) as db: db.execute("ALTER TABLE schedules DROP COLUMN last_error_details")
    after=ExpiryQueue(path,clock=clock).get(row["id"])
    assert (after["deadline_ms"],after["attempts"],after["state"])==(before["deadline_ms"],before["attempts"],before["state"])


def test_expiry_failures_retain_sanitized_actionable_diagnostics(tmp_path,monkeypatch):
    import httpx
    clock=Clock();queue=ExpiryQueue(tmp_path/"expiry.sqlite3",clock=clock);row=reserve(queue)
    secret="fixture-secret X-BAPI-SIGN api-key=must-not-persist"
    mode=["auth"]
    def response(method,path,params):
        if mode[0]=="timeout": raise httpx.ReadTimeout(secret)
        if mode[0]=="auth": return httpx.Response(401,json={"retCode":10003,"retMsg":secret,"headers":secret})
        if mode[0]=="api": return httpx.Response(200,json={"retCode":10002,"retMsg":secret})
        if mode[0]=="json": return httpx.Response(200,text=secret)
        if mode[0]=="cancel-timeout" and method=="POST": raise httpx.ReadTimeout(secret)
        return httpx.Response(200,json={"retCode":0,"result":{"category":"linear","nextPageCursor":"x","list":[order(row)]}})
    calls=_repair_http(monkeypatch,response);clock.advance(1001);engine=ExpiryEngine(queue)
    saved=engine.run_once();d=json.loads(saved["last_error_details"])
    assert d=={"operation":"realtime","reason":"http_error","exception_type":"BrokerError","http_status":401,"ret_code":10003}
    assert "credentials" in saved["last_error"] and saved["attempts"]==0
    with queue.connection() as db: count=db.execute("SELECT count(*) FROM evidence").fetchone()[0]
    clock.advance();engine.run_once()
    with queue.connection() as db: assert db.execute("SELECT count(*) FROM evidence").fetchone()[0]==count
    for value,reason in (("api","api_error"),("json","invalid_json"),("timeout","transport_error")):
        mode[0]=value;clock.advance();saved=engine.run_once()
        assert json.loads(saved["last_error_details"])["reason"]==reason and saved["attempts"]==0
        assert saved["next_check_ms"]-clock()<=60000
    mode[0]="cancel-timeout";clock.advance();saved=engine.run_once()
    assert saved["attempts"]==1 and saved["state"]=="reconciling"
    assert json.loads(saved["last_error_details"])["operation"]=="cancel"
    assert json.loads(saved["last_error_details"])["exception_type"]=="ReadTimeout"
    with queue.connection() as db:
        evidence=[dict(r) for r in db.execute("SELECT * FROM evidence")]
    assert secret not in json.dumps(evidence)+json.dumps(saved)
    assert "X-BAPI" not in json.dumps(evidence)+json.dumps(saved)
    assert len([c for c in calls if c[0]=="POST"])==1


def test_expiry_repair_worker_retains_independent_lifecycle(tmp_path):
    from tools.bybit_order_expiry_worker import stop_worker
    path,log=tmp_path/"empty.sqlite3",tmp_path/"worker.log"
    script="from tools.bybit_order_expiry_worker import start_worker; import json,sys,pathlib; s=start_worker(sys.argv[1],log_path=sys.argv[2],fixture_seconds=18); pathlib.Path(sys.argv[3]).write_text('local master exiting'); print(json.dumps(s))"
    status=None
    try:
        parent=subprocess.run([sys.executable,"-c",script,str(path),str(log),str(tmp_path/"local-exit-marker")],cwd=ROOT,
                              env=dict(os.environ,PYTHONPATH=str(ROOT),MASTER_ENV_FILE=str(tmp_path/"absent-env")),capture_output=True,text=True,timeout=20)
        assert parent.returncode==0,parent.stderr
        status=json.loads(parent.stdout);queue=ExpiryQueue(path)
        current=queue.worker_status()
        assert current["healthy"] and current["worker"]["instance"]==status["worker"]["instance"]
        assert current["pending"]==0 and (tmp_path/"local-exit-marker").exists()
        assert log.read_text().count("Expiry worker started")==1
        stop_worker(path,instance=current["worker"]["instance"])
        end=time.monotonic()+4
        while time.monotonic()<end and queue.worker_status()["ownership_locked"]: time.sleep(.1)
        assert not queue.worker_status()["ownership_locked"]
        assert "Expiry worker stopped" in log.read_text()
    finally:
        if status:
            current=ExpiryQueue(path).worker_status()
            if current["healthy"] and current["worker"]["instance"]==status["worker"]["instance"]:
                stop_worker(path,instance=status["worker"]["instance"])
