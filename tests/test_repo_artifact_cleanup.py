"""Two bounded fixture-only contracts for the actual Python cleanup entry point."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import _winapi

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / 'tools/cleanup_pytest_temp.py'
WORK = REPO / '.codex-work/active/housekeeping-lifecycle-20261103/fixtures'


def identity(p):
    s = p.lstat()
    return dict(dev=s.st_dev, ino=s.st_ino, size=s.st_size,
                mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns,
                attributes=s.st_file_attributes)


def fixture():
    root = WORK / ('case-' + uuid.uuid4().hex) / 'checkout'
    WORK.parent.mkdir(parents=True,exist_ok=True)
    with (WORK.parent/'fixture-roots.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(dict(path=root.parent.relative_to(REPO).as_posix(),pid=os.getpid(),kind='isolated synthetic test fixture',creation_source=str(Path(__file__).relative_to(REPO))))+'\n')
    (root / '.git').mkdir(parents=True)
    (root / '.codex-work').mkdir()
    (root / 'AGENTS.md').write_text('fixture checkout')
    (root / '.job-fixture/nested').mkdir(parents=True)
    (root / '.job-fixture/nested/item').write_bytes(b'disposable')
    return root


def plan(root, paths):
    entries = []
    for rel in paths:
        p = root / rel
        entries.append(dict(path=rel, type='directory' if p.is_dir() else 'file',
                            size=p.stat().st_size if p.is_file() else 0,
                            identity=identity(p), decision='delete',
                            evidence='test-created fixture', status='completed', reason='test cleanup'))
    return dict(schema=1, root=str(root), root_identity=identity(root), entries=entries)


def run(root, data=None, mode='Apply', extra=()):
    args = [sys.executable, str(HELPER), '--repo-root', str(root), '--mode', mode]
    if data is not None:
        p = root / '.codex-work' / (uuid.uuid4().hex + '.json')
        p.write_text(json.dumps(data))
        args += ['--manifest', str(p)]
    args += list(extra)
    result = root / '.codex-work' / (uuid.uuid4().hex + '-result.json')
    args += ['--result', str(result)]
    child = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert result.is_file(), (child.returncode, child.stdout, child.stderr)
    body = json.loads(result.read_text())
    assert child.returncode == (0 if body['ok'] else 1), (child.stdout, child.stderr, body)
    return body


def accepted(root, paths):
    data=plan(root,paths);job='.codex-work/retained/accepted-fixture'
    data['accepted_retention_release']=[dict(job=job,completed=True,accepted=True,
        release_condition_ended=True,completion_evidence='fixture completed',
        acceptance_evidence='explicit user Pass',release_condition='acceptance ended')]
    for entry in data['entries']:
        entry.update(status='accepted',release_condition='acceptance ended',
            accepted_release=dict(job=job,generated=True,unresolved=False,
                release_condition_ended=True,kind='candidate',reason='generated fixture accepted'))
    return data


def registered_fixture():
    """An independent, normally exited creator supplies exact object provenance."""
    root=fixture(); external=root.parent/'CODEX-job9-backups'
    token=uuid.uuid4().hex
    synthetic='.codex-work/active/demo/fixtures/isolated'
    paths=['CODEX_BRIEF_finished.txt','.job_targeted_pytest.log','targeted_pytest.xml',
           'summary_stage1.patch','instructions.pointer','counter.txt','.pnl_calendar_review/review.patch',
           synthetic+'/.git/config',synthetic+'/data/live.sqlite',synthetic+'/notes/mock.lock','.codex-work/retained/accepted-fixture/prior-result.json']
    code="""import json,sys,ctypes,os,pathlib
root=pathlib.Path(sys.argv[1]);external=pathlib.Path(sys.argv[2]);paths=json.loads(sys.argv[3])
for rel in paths:
 p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'synthetic generated fixture')
external.mkdir();(external/'snapshot.original').write_bytes(b'task-owned backup copy')
from ctypes import wintypes as w
k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=w.HANDLE;k.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
t=[w.FILETIME() for _ in range(4)];assert k.GetProcessTimes(k.GetCurrentProcess(),*[ctypes.byref(x) for x in t])
print(json.dumps(dict(pid=os.getpid(),process_start_filetime=(t[0].dwHighDateTime<<32)|t[0].dwLowDateTime)))
"""
    child=subprocess.run([sys.executable,'-B','-c',code,str(root),str(external),json.dumps(paths)],capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert child.returncode==0,child.stderr
    done=dict(json.loads(child.stdout),kind='artifact-completion',token=token,completed=True,exit=child.returncode)
    completion=root/'.codex-work/completion.json';completion.write_text(json.dumps(done),encoding='utf-8')
    import hashlib
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    common=dict(schema=1,kind='artifact-registration',job='finished-fixture',token=token,
        completed=True,completion_evidence='normal creator OS exit0',owner_finished=True,
        creation_evidence='separate creator subprocess generated these exact bytes',
        completion_receipt=dict(path='.codex-work/completion.json',sha256=sha(completion)))
    records={}
    for key,rels,base in [('local',paths,root),('external',['snapshot.original'],external)]:
        record=dict(common,objects={rel:dict(root_id='checkout' if key=='local' else 'backup',identity=identity(base/rel),sha256=sha(base/rel),kind='fixture' if rel.startswith(synthetic) else 'backup' if key=='external' else 'brief' if rel.endswith('.txt') else 'log',synthetic=rel.startswith(synthetic)) for rel in rels})
        if key=='local':record.update(isolated_fixture=True,fixture_path=synthetic)
        else:record.update(root=str(external),root_identity=identity(external),task_owned=True)
        q=root/'.codex-work'/('creation-'+key+'.json');q.write_text(json.dumps(record),encoding='utf-8');records[key]=dict(path=q.relative_to(root).as_posix(),sha256=sha(q))
    data=plan(root,[rel for rel in paths if 'prior-result' not in rel]);data['registrations']=records
    for e in data['entries']:e['artifact_release']=dict(registration='local',job='finished-fixture',need_ended=True,owner_finished=True)
    external_entry=plan(external,['snapshot.original'])['entries'][0];external_entry.update(root_id='backup',artifact_release=dict(registration='external',job='finished-fixture',need_ended=True,owner_finished=True));data['entries'].append(external_entry)
    data['external_roots']=[dict(id='backup',path=str(external),identity=identity(external),registration='external',explicitly_authorized=True)]
    data['synthetic_fixtures']={'local':dict(path=synthetic,identity=identity(root/synthetic))}
    return root,data,external


def test_manifest_confinement_protection_reparse_and_audit():
    root = fixture(); rel = '.job-fixture/nested/item'; item = root / rel
    data = plan(root, [rel])
    assert run(root, data, 'Audit')['ok'] and item.read_bytes() == b'disposable'
    assert run(root, mode='Audit')['ok'] and item.exists()
    shallow=run(root,mode='Audit');assert not any(e['path'].startswith('.git/') or e['path'].startswith('.job-fixture/') for e in shallow['entries'])
    excluded=run(root,mode='Audit',extra=['--audit-root','.job-fixture','--exclude','.job-fixture/nested']);assert not any(e['path'].startswith('.job-fixture/nested/') for e in excluded['entries'])
    assert not run(root)['ok'] and item.exists()
    retained='.codex-work/retained/accepted-fixture/candidate.xlsx'
    (root/retained).parent.mkdir(parents=True);(root/retained).write_bytes(b'generated')
    assert not run(root,plan(root,[rel,retained]))['ok'] and item.exists()
    release=accepted(root,[retained])
    assert run(root,release,'Audit')['ok'] and (root/retained).exists()
    for bad in ['.codex-work/retained/other-job/candidate.xlsx',
                '.codex-work/retained/accepted-fixture/apply.claim',
                '.codex-work/retained/accepted-fixture/transaction.json',
                '.codex-work/retained/accepted-fixture/native-final-result.json',
                '.codex-work/retained/accepted-fixture/notes/unique.txt']:
        (root/bad).parent.mkdir(parents=True,exist_ok=True);(root/bad).write_bytes(b'protected')
        assert not run(root,accepted(root,[retained,bad]))['ok'] and (root/retained).exists()
    unresolved=accepted(root,[retained]);unresolved['entries'][0]['accepted_release']['unresolved']=True
    assert not run(root,unresolved)['ok'] and (root/retained).exists()
    unaccepted=accepted(root,[retained]);unaccepted['accepted_retention_release'][0]['accepted']=False
    assert not run(root,unaccepted)['ok'] and (root/retained).exists()
    for bad in ['../outside', str(item), '.job-fixture/../AGENTS.md']:
        altered = json.loads(json.dumps(data)); altered['entries'][0]['path'] = bad
        assert not run(root, altered)['ok'] and item.exists()
    (root / 'render/data').mkdir(parents=True)
    (root / 'render/data/live.sqlite').write_bytes(b'protected')
    protected = plan(root, [rel, 'render/data/live.sqlite'])
    assert not run(root, protected)['ok'] and item.exists()  # whole-plan preflight
    for protected_rel in ['AGENTS.md', '.git', 'render/data/live.sqlite']:
        assert not run(root, plan(root, [rel, protected_rel]))['ok'] and item.exists()
    wrong = json.loads(json.dumps(data)); wrong['root_identity']['ino'] += 1
    assert not run(root, wrong)['ok'] and item.exists()
    link = root / '.job-fixture/link'
    _winapi.CreateJunction(str(root / '.job-fixture/nested'), str(link))
    try:
        linked = plan(root, ['.job-fixture/link/item'])
        assert not run(root, linked)['ok'] and item.exists()
        audit = run(root, mode='Audit', extra=['--audit-root','.job-fixture'])
        assert any(e['path']=='.job-fixture/link' and e['type']=='reparse' for e in audit['entries'])
        assert not any(e['path'].startswith('.job-fixture/link/') for e in audit['entries'])
    finally:
        os.rmdir(link)  # remove only the test-created junction, never its target


    registered_root,release,external=registered_fixture()
    assert run(registered_root,release,'Audit')['ok']
    # Alive owner evidence must not be accepted as a finished fixture process.
    import ctypes,hashlib
    from ctypes import wintypes as w
    completion=registered_root/'.codex-work/completion.json';original_completion=completion.read_bytes()
    creation=registered_root/'.codex-work/creation-local.json';original_creation=creation.read_bytes()
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=w.HANDLE;k.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)];times=[w.FILETIME() for _ in range(4)];assert k.GetProcessTimes(k.GetCurrentProcess(),*[ctypes.byref(x) for x in times])
    active=json.loads(original_completion);active.update(pid=os.getpid(),process_start_filetime=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime);completion.write_text(json.dumps(active),encoding='utf-8')
    record=json.loads(original_creation);record['completion_receipt']['sha256']=hashlib.sha256(completion.read_bytes()).hexdigest();creation.write_text(json.dumps(record),encoding='utf-8')
    alive=json.loads(json.dumps(release));alive['registrations']['local']['sha256']=hashlib.sha256(creation.read_bytes()).hexdigest()
    assert not run(registered_root,alive)['ok']
    completion.write_bytes(original_completion);creation.write_bytes(original_creation)
    # An obsolete result is not permanent merely because of its filename. It needs
    # accepted release, exact creation proof and an immutable superseding closeout.
    old='.codex-work/retained/accepted-fixture/prior-result.json'
    retired=accepted(registered_root,[old]);retired['registrations']=release['registrations']
    retired['entries'][0]['artifact_release']=release['entries'][0]['artifact_release']
    assert not run(registered_root,retired)['ok']
    successor=registered_root/'.codex-work/final-receipt.json';successor.write_text(json.dumps(dict(kind='job-closeout',completed=True,retired_artifacts=[old])),encoding='utf-8')
    retired['entries'][0]['accepted_release'].update(kind='redundant-evidence',replay_protection=False,superseded_by='.codex-work/final-receipt.json',superseded_sha256=hashlib.sha256(successor.read_bytes()).hexdigest())
    assert run(registered_root,retired,'Audit')['ok'] and (registered_root/old).exists()
    assert run(registered_root,retired)['ok'] and not (registered_root/old).exists()

    # A manifest alone, a blanket fixture root, or a name is not creation proof.
    for alter in ['registration','scope','external','owner']:
        wrong=json.loads(json.dumps(release))
        if alter=='registration':wrong['registrations']['local']['sha256']='0'*64
        elif alter=='scope':wrong['synthetic_fixtures']['local']['path']='.codex-work/active/demo'
        elif alter=='external':wrong['external_roots'][0]['path']=str(registered_root.parent)
        else:wrong['entries'][0]['artifact_release']['owner_finished']=False
        assert not run(registered_root,wrong)['ok']
        assert (registered_root/'CODEX_BRIEF_finished.txt').exists()
    user='.codex-work/active/demo/fixtures/isolated/notes/user.txt';(registered_root/user).write_bytes(b'unique user content')
    bad=json.loads(json.dumps(release));entry=plan(registered_root,[user])['entries'][0];entry['artifact_release']=release['entries'][0]['artifact_release'];bad['entries'].append(entry)
    assert not run(registered_root,bad)['ok'] and (registered_root/user).exists()
    replaced=json.loads(json.dumps(release));replaced['external_roots'][0]['identity']['ino']+=1
    assert not run(registered_root,replaced)['ok'] and (external/'snapshot.original').exists()


def test_exact_removal_empty_directories_identity_and_failure_status():
    root = fixture(); rel = '.job-fixture/nested/item'; item = root / rel
    data = plan(root, [rel]); item.write_bytes(b'changed')
    assert not run(root, data)['ok'] and item.read_bytes()==b'changed'
    retained='.codex-work/retained/accepted-fixture/candidate.xlsx'
    (root/retained).parent.mkdir(parents=True);(root/retained).write_bytes(b'generated')
    release=accepted(root,[retained]);(root/retained).write_bytes(b'changed identity metadata')
    assert not run(root,release)['ok'] and (root/retained).exists()
    released=run(root,accepted(root,[retained,'.codex-work/retained/accepted-fixture']))
    assert released['ok'] and len(released['results'])==2 and not (root/retained).parent.exists()
    data = plan(root, [rel]); item.unlink(); item.write_bytes(b'changed')
    assert not run(root, data)['ok'] and item.exists()
    assert not run(root, plan(root, ['.job-fixture/nested']))['ok'] and item.exists()
    extra = root / '.job-fixture/readonly'; extra.write_bytes(b'locked fixture')
    import ctypes
    ctypes.windll.kernel32.SetFileAttributesW(str(extra), 1)
    try:
        before=root/'.job-fixture/aaa';before.write_bytes(b'first')
        skipped=root/'.job-fixture/zzz';skipped.write_bytes(b'skipped')
        failed = run(root, plan(root, ['.job-fixture/aaa','.job-fixture/readonly','.job-fixture/zzz']))
        assert not failed['ok'] and failed['results'][0]['status']=='deleted' and failed['results'][1]['status']=='failed' and extra.exists()
        assert failed['results'][2]['status']=='skipped' and failed['deleted_bytes']==5
    finally:
        ctypes.windll.kernel32.SetFileAttributesW(str(extra), 128)
    final=plan(root, [rel, '.job-fixture/readonly', '.job-fixture/zzz', '.job-fixture/nested', '.job-fixture'])
    success = run(root, final)
    assert success['ok'] and success['deleted_bytes']==len(b'changed')+len(b'locked fixture')+len(b'skipped')
    assert len(success['results'])==5 and not (root / '.job-fixture').exists()
    reconciled=run(root,final,'Audit',extra=['--reconcile']);assert reconciled['ok'] and reconciled['reconciled']
    new=root/'.job-new';new.mkdir();(new/'unregistered').write_bytes(b'generated but not registered')
    final['reconcile_roots']=[dict(path='.job-new',identity=identity(new))]
    observed=run(root,final,'Audit',extra=['--reconcile']);assert not observed['ok'] and '.job-new/unregistered' in observed['unregistered']

    generated,release,external=registered_fixture()
    external_plan=json.loads(json.dumps(release));(external/'snapshot.original').write_bytes(b'changed backing identity')
    assert not run(generated,external_plan)['ok'] and (generated/'CODEX_BRIEF_finished.txt').exists()
    # Fresh exact registered fixture release removes synthetic Git/data/notes/lock
    # objects, not the real checkout's protected counterparts. No recursive removal.
    generated,release,external=registered_fixture()
    audited=run(generated,release,'Audit');assert audited['ok'] and (external/'snapshot.original').exists()
    removed=run(generated,release)
    assert removed['ok'] and len(removed['results'])==len(release['entries'])
    assert not (external/'snapshot.original').exists()
    assert all(not (generated/e['path']).exists() for e in release['entries'] if e.get('root_id','checkout')=='checkout')
    assert (generated/'.git').is_dir() and (generated/'AGENTS.md').is_file()
