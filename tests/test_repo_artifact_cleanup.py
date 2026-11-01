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
WORK = REPO / '.codex-work/active/accepted-repair-closeout-20261101'


def identity(p):
    s = p.lstat()
    return dict(dev=s.st_dev, ino=s.st_ino, size=s.st_size,
                mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns,
                attributes=s.st_file_attributes)


def fixture():
    root = WORK / ('fixture-' + uuid.uuid4().hex)
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


def run(root, data=None, mode='Apply'):
    args = [sys.executable, str(HELPER), '--repo-root', str(root), '--mode', mode]
    if data is not None:
        p = root / '.codex-work' / (uuid.uuid4().hex + '.json')
        p.write_text(json.dumps(data))
        args += ['--manifest', str(p)]
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


def test_manifest_confinement_protection_reparse_and_audit():
    root = fixture(); rel = '.job-fixture/nested/item'; item = root / rel
    data = plan(root, [rel])
    assert run(root, data, 'Audit')['ok'] and item.read_bytes() == b'disposable'
    assert run(root, mode='Audit')['ok'] and item.exists()
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
        audit = run(root, mode='Audit')
        assert any(e['path']=='.job-fixture/link' and e['type']=='reparse' for e in audit['entries'])
        assert not any(e['path'].startswith('.job-fixture/link/') for e in audit['entries'])
    finally:
        os.rmdir(link)  # remove only the test-created junction, never its target


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
    success = run(root, plan(root, [rel, '.job-fixture/readonly', '.job-fixture/zzz', '.job-fixture/nested', '.job-fixture']))
    assert success['ok'] and success['deleted_bytes']==len(b'changed')+len(b'locked fixture')+len(b'skipped')
    assert len(success['results'])==5 and not (root / '.job-fixture').exists()
