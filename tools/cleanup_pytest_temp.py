"""Exact-manifest repository artifact cleanup. Default Audit cannot delete.

Use the established Python runtime directly; the .ps1 is only a compatibility wrapper.
Schema 1 uses root/root_identity and reviewed per-path type/size/identity/evidence/
status/reason/decision. Apply requires --manifest and a new private --result receipt.
Windows deletion is by a verified open handle, with parents pinned against renames;
only empty listed directories can be marked for deletion. Never force or retry.
References: https://docs.python.org/3.13/library/os.html#os.lstat
https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle
"""


def main():
    import json, os, stat, sys, time, re, hashlib
    from pathlib import Path, PurePosixPath
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', '-RepoRoot', default=os.getcwd())
    parser.add_argument('--mode', '-Mode', choices=('Audit','Apply'), default='Audit')
    parser.add_argument('--manifest', '-Manifest', default='')
    parser.add_argument('--result', '-Result', default='')
    parser.add_argument('--reconcile', action='store_true', help='Audit final disposition and bounded registered job roots without deletion')
    parser.add_argument('--audit-root', action='append', default=[], help='Exact managed job root to inspect; default Audit is shallow')
    parser.add_argument('--exclude', action='append', default=[], help='Known inaccessible relative path; prune before stat/open')
    args=parser.parse_args()
    root_arg,mode,manifest_arg,result_arg=args.repo_root,args.mode,args.manifest,args.result
    started=time.monotonic()
    receipt=dict(mode=mode,ok=False,results=[],deleted_bytes=0)
    event_stream=None
    def event(value):
        if event_stream is not None:
            event_stream.write(json.dumps(value)+'\n');event_stream.flush();os.fsync(event_stream.fileno())
    def deadline():
        if time.monotonic()-started>280: raise TimeoutError('cleanup deadline')
    def identity(p):
        s=p.lstat()
        return dict(dev=s.st_dev,ino=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns,attributes=getattr(s,'st_file_attributes',0))
    def ancestry(p):
        for q in [p,*p.parents]:
            s=q.lstat()
            if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&1024: raise ValueError('reparse/link ancestry: '+str(q))
    roots={}
    registrations={}
    locked_hashes={}
    fixture_roots={}
    def base_for(e):
        key=e.get("root_id","checkout")
        if key not in roots: raise ValueError("undeclared root")
        return roots[key]
    def target(rel, base=None):
        base=root if base is None else base
        if not isinstance(rel,str) or chr(92) in rel or ':' in rel: raise ValueError('noncanonical relative path')
        parts=PurePosixPath(rel).parts
        if not parts or rel!='/'.join(parts) or any(x in ('.','..','') for x in parts) or rel.startswith('/'): raise ValueError('escaping/noncanonical path: '+rel)
        q=base.joinpath(*parts)
        if os.path.commonpath([base,q])!=str(base) or q==base: raise ValueError('outside root')
        ancestry(q)
        return q
    def proof(spec):
        q=target(spec.get('path',''))
        if q.suffix!='.json' or '.codex-work' not in q.parts: raise ValueError('provenance must be private JSON')
        raw=q.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=spec.get('sha256'): raise ValueError('changed provenance')
        protected_receipts.add(q)
        value=json.loads(raw)
        if value.get('schema')!=1 or value.get('kind')!='artifact-registration': raise ValueError('not creation provenance')
        return value
    def owner_completed(record):
        spec=record.get('completion_receipt',{})
        q=target(spec.get('path',''));raw=q.read_bytes();protected_receipts.add(q)
        if hashlib.sha256(raw).hexdigest()!=spec.get('sha256'): raise ValueError('changed completion evidence')
        done=json.loads(raw)
        if done.get('kind')!='artifact-completion' or done.get('token')!=record.get('token') or done.get('completed') is not True or done.get('exit')!=0: return False
        pid=done.get('pid');started=done.get('process_start_filetime')
        if not isinstance(pid,int) or pid<=0 or not isinstance(started,int) or pid==os.getpid(): return False
        if os.name!='nt': return False
        import ctypes
        from ctypes import wintypes as w
        k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE;k.CloseHandle.argtypes=[w.HANDLE];k.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)];k.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]
        h=k.OpenProcess(0x1000,False,pid)
        if not h: return ctypes.get_last_error() in (87,1168)
        try:
            times=[w.FILETIME() for _ in range(4)];code=w.DWORD()
            if not k.GetProcessTimes(h,*[ctypes.byref(x) for x in times]) or not k.GetExitCodeProcess(h,ctypes.byref(code)): return False
            actual=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
            return actual!=started or code.value!=259
        finally:k.CloseHandle(h)
    def registered(e, *, synthetic=False):
        release=e.get('artifact_release',{})
        record=registrations.get(release.get('registration'))
        if not record or record.get('job')!=release.get('job'): return False
        if release.get('need_ended') is not True or release.get('owner_finished') is not True: return False
        if record.get('completed') is not True or not record.get('completion_evidence') or not owner_completed(record): return False
        obj=record.get('objects',{}).get(e['path'])
        if not obj or obj.get('root_id','checkout')!=e.get('root_id','checkout'): return False
        if obj.get('identity')!=e['identity'] or obj.get('kind') not in {'brief','log','xml','patch','pointer','fixture','backup','candidate','helper','bytecode'}: return False
        if synthetic and (obj.get('synthetic') is not True or not record.get('isolated_fixture')): return False
        if e['type']=='file':
            q=target(e['path'],base_for(e))
            actual=locked_hashes[q] if q in locked_hashes else hashlib.sha256(q.read_bytes()).hexdigest()
            if actual!=obj.get('sha256'): return False
        return True
    def synthetic_release(e):
        rel=e['path'];job=e.get('artifact_release',{}).get('registration');fixture=fixture_roots.get(job)
        if not fixture or e.get('root_id','checkout')!='checkout': return False
        if not (rel==fixture['path'] or rel.startswith(fixture['path']+'/')): return False
        q=target(fixture['path'])
        if any(identity(q)[k]!=fixture['identity'][k] for k in ('dev','ino','attributes')): return False
        return registered(e,synthetic=True)
    accepted_jobs={}
    def accepted_release(e):
        rel=e['path']; parts=rel.split('/')
        if len(parts)<3 or parts[:2]!=['.codex-work','retained']: return False
        job='/'.join(parts[:3]); authorization=accepted_jobs.get(job)
        release=e.get('accepted_release',{})
        if not authorization or release.get('job')!=job: return False
        if release.get('generated') is not True or release.get('release_condition_ended') is not True or release.get('unresolved') is not False: return False
        if release.get('kind') not in {'fixture','candidate','generated-baseline','superseded-helper','accepted-recovery','duplicate-inventory','redundant-evidence'}: return False
        if not isinstance(release.get('reason'),str) or not release['reason'].strip(): return False
        name=parts[-1].lower()
        if name.endswith('.claim') or name in {'transaction.json','ledger.json'} or 'ledger' in name: return False
        if name.endswith('.digest') and release.get('kind')!='redundant-evidence': return False
        if 'final-result' in name or 'final-receipt' in name: return False
        if 'result' in name or 'receipt' in name or name.endswith(('.events.jsonl','.digest')):
            if release.get('kind')!='redundant-evidence' or release.get('replay_protection') is not False: return False
            if not registered(e): return False
            successor=target(release.get('superseded_by',''))
            if successor==target(rel) or not successor.is_file(): return False
            raw=successor.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=release.get('superseded_sha256'): return False
            closeout=json.loads(raw)
            if closeout.get('kind')!='job-closeout' or closeout.get('completed') is not True or rel not in closeout.get('retired_artifacts',[]): return False
            protected_receipts.add(successor)
        if e.get('status')!='accepted' or not e.get('release_condition'): return False
        return True
    def permitted(rel,e):
        parts=rel.lower().split('/')
        if synthetic_release(e): return True
        if parts[-1].endswith('.claim') or 'ledger' in parts[-1] or parts[-1]=='transaction.json' or 'final-result' in parts[-1] or 'final-receipt' in parts[-1]: return False
        if parts[-1].endswith('.digest') and not accepted_release(e): return False
        if e.get('root_id','checkout')!='checkout':
            return registered(e) and not any(x in {'.git','.venv','venv','node_modules','notes','charts','uploads','credentials'} for x in parts)
        if any(x in {'.git','.venv','venv','node_modules','.runtime','uploads','archives','credentials','notes','charts','raw','history','data'} or x.startswith('.env') or x.endswith(('.pem','.key')) for x in parts): return False
        if parts[0] in {'journal','page-video-downloader'}: return False
        if parts[-1].endswith(('.lock','-wal','-shm','-journal')) or parts[-1] in {'settings.json','credentials.json','state.json'}: return False
        if parts[0] in {'render','bybit_monitor','oanda_monitor'}:
            return '__pycache__' in parts and (parts[-1]=='__pycache__' or parts[-1].endswith('.pyc'))
        if parts[0]=='.codex-work': return len(parts)>=3 and (parts[1]=='active' or accepted_release(e))
        if '__pycache__' in parts: return parts[-1]=='__pycache__' or parts[-1].endswith('.pyc')
        if parts[0].startswith(('.job','.pytest','.codex_tmp','.counter_trend_repair_')): return True
        if parts[0].startswith(('.pnl_calendar_','.position_match_','.position_sl','.sl_reload_')): return registered(e)
        if len(parts)==1 and (re.fullmatch(r'codex_brief_.+\.txt',parts[0]) or parts[0].endswith(('.log','.xml','.patch','.pointer','.txt'))):
            return registered(e)
        return False
    def check(e,directory_after=False):
        deadline()
        base=base_for(e); expected_root=root_identities[e.get('root_id','checkout')]
        if any(identity(base)[k]!=expected_root[k] for k in ('dev','ino','attributes')): raise ValueError('root identity changed')
        q=target(e['path'],base)
        if q in protected_receipts: raise ValueError('current/provenance/replay receipt is protected')
        if not permitted(e['path'],e): raise ValueError('protected path: '+e['path'])
        s=q.lstat();typ=e['type']
        if typ not in ('file','directory') or (typ=='file' and not stat.S_ISREG(s.st_mode)) or (typ=='directory' and not stat.S_ISDIR(s.st_mode)): raise ValueError('unexpected type')
        now=identity(q);expected=e['identity'];keys=('dev','ino','attributes') if directory_after else tuple(now)
        if any(now[k]!=expected[k] for k in keys): raise ValueError('identity/metadata changed: '+e['path'])
        if typ=='file' and now['size']!=e['size']: raise ValueError('size changed')
        if not all(isinstance(e.get(k),str) and e[k].strip() for k in ('evidence','status','reason')): raise ValueError('missing review evidence')
        return q
    def reconcile(plan):
        if mode!='Audit': raise ValueError('reconciliation is Audit only')
        observed=[];known=set();problems=[];unregistered=[]
        for e in plan['entries']:
            key=e.get('root_id','checkout');base=roots.get(key)
            if base is None: raise ValueError('undeclared reconcile root')
            rel=e['path'];parts=PurePosixPath(rel).parts
            if not parts or rel!='/'.join(parts) or any(x in ('.','..') for x in parts) or ':' in rel or chr(92) in rel or rel.startswith('/'): raise ValueError('escaping reconcile target')
            known.add((key,rel))
            if e.get('uninspected') is True:
                if e.get('decision')!='retain' or not e.get('reason'): raise ValueError('uninspected retention reason missing')
                observed.append(dict(root_id=key,path=rel,status='retained-uninspected',reason=e['reason']));continue
            q=base.joinpath(*parts);ancestor=q.parent
            while not ancestor.exists() and not ancestor.is_symlink(): ancestor=ancestor.parent
            ancestry(ancestor)
            present=q.exists() or q.is_symlink()
            if e.get('decision')=='delete' and present: problems.append(dict(path=rel,error='registered disposal remains'))
            if e.get('decision')=='retain' and (not e.get('reason') or not e.get('release_condition')): problems.append(dict(path=rel,error='retention reason/release condition missing'))
            observed.append(dict(root_id=key,path=rel,status='retained' if present else 'absent'))
        for spec in plan.get('reconcile_roots',[]):
            rel=spec['path']
            if not rel.startswith(('.codex-work/active/','.codex-work/retained/','.job')): raise ValueError('unbounded reconciliation root')
            p=root.joinpath(*PurePosixPath(rel).parts)
            if not p.exists(): continue
            p=target(rel)
            if any(identity(p)[k]!=spec['identity'][k] for k in ('dev','ino','attributes')): raise ValueError('reconciliation root replaced')
            stack=[p];count=0;excluded=set(spec.get('excluded',[]))
            while stack:
                q=stack.pop()
                with os.scandir(q) as it:
                    for child in it:
                        deadline();count+=1
                        if count>5000: raise ValueError('bounded reconciliation limit')
                        name=Path(child.path).relative_to(root).as_posix()
                        if any(name==x or name.startswith(x+'/') for x in excluded): continue
                        v=child.stat(follow_symlinks=False)
                        if ('checkout',name) not in known: unregistered.append(name)
                        if not (stat.S_ISLNK(v.st_mode) or getattr(v,'st_file_attributes',0)&1024) and stat.S_ISDIR(v.st_mode) and child.name.lower() not in {'.git','data','notes','charts','uploads','archives','credentials','.venv','venv','node_modules'}: stack.append(Path(child.path))
        receipt.update(results=observed,unregistered=unregistered,errors=problems,ok=not problems and not unregistered,reconciled=True)
    # Windows handle ownership prevents rename/replacement between recheck and delete.
    # No privilege, permissions, attributes, or execution-policy changes are made.
    def guarded_remove(q,e,ancestor_ids):
        if os.name!='nt': raise OSError('Apply is supported only on this Windows checkout')
        import ctypes
        from ctypes import wintypes
        import msvcrt
        from contextlib import ExitStack
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        create=kernel.CreateFileW
        create.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
        create.restype=wintypes.HANDLE
        close=kernel.CloseHandle;close.argtypes=(wintypes.HANDLE,);close.restype=wintypes.BOOL
        dispose=kernel.SetFileInformationByHandle
        dispose.argtypes=(wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD);dispose.restype=wintypes.BOOL
        class Disposition(ctypes.Structure): _fields_=[('DeleteFile',wintypes.BOOLEAN)]
        def opened(path,delete=False):
            # OPEN_EXISTING, OPEN_REPARSE_POINT, BACKUP_SEMANTICS; never create.
            h=create(str(path),0x80 | (0x10000 if delete else 0) | (0x80000000 if delete and e['type']=='file' else 0),3 if not delete else 1,None,3,0x02200000,None)
            if h==wintypes.HANDLE(-1).value: raise ctypes.WinError(ctypes.get_last_error())
            return h
        base=base_for(e)
        with ExitStack() as stack:
            # Lock root outward-to-inward. Share READ/WRITE but not DELETE for parents.
            parents=[]
            for ancestor in q.parents:
                if ancestor==base.parent: break
                parents.append(ancestor)
            for ancestor in reversed(parents):
                h=opened(ancestor);stack.callback(close,h)
                now=identity(ancestor);old=ancestor_ids[str(ancestor)]
                if any(now[k]!=old[k] for k in ('dev','ino','attributes')): raise ValueError('ancestor identity changed')
                ancestry(ancestor)
            h=opened(q,True)
            try: fd=msvcrt.open_osfhandle(h,os.O_RDONLY | os.O_NOINHERIT)
            except Exception:
                close(h);raise
            stack.callback(os.close,fd)
            s=os.fstat(fd);expected=e['identity']
            # fstat identifies the exact handle; lstat below checks the exact path too.
            if s.st_ino!=expected['ino'] or s.st_dev!=expected['dev']: raise ValueError('opened candidate identity changed')
            if e['type']=='file':
                # Read the pinned resource itself; reopening its name would conflict
                # with our DELETE handle and would also introduce a replacement race.
                with os.fdopen(os.dup(fd),'rb') as owned_source:
                    locked_hashes[q]=hashlib.file_digest(owned_source,'sha256').hexdigest()
            try:
                check(e,directory_after=e['type']=='directory')
                if e['type']=='directory':
                    with os.scandir(q) as it:
                        if next(it,None) is not None: raise ValueError('directory is not empty')
                disposition=Disposition(1)
                if not dispose(h,4,ctypes.byref(disposition),ctypes.sizeof(disposition)): raise ctypes.WinError(ctypes.get_last_error())
            finally: locked_hashes.pop(q,None)
        # Closing the verified DELETE handle completes that individual deletion.
        if q.exists(): raise OSError('deletion did not complete')


    try:
        root=Path(os.path.abspath(root_arg));ancestry(root);root_expected=identity(root)
        roots['checkout']=root;root_identities={'checkout':root_expected};protected_receipts=set()
        if mode=='Apply' and not result_arg: raise ValueError('Apply requires a new private Result receipt')
        if not (root/'.git').is_dir() or not (root/'AGENTS.md').is_file(): raise ValueError('not a checkout root')
        ancestry(root/'.git');ancestry(root/'AGENTS.md')
        if result_arg:
            rp=Path(os.path.abspath(result_arg));ancestry(rp.parent)
            if os.path.commonpath([root,rp])!=str(root) or '.codex-work' not in rp.parts or rp.exists(): raise ValueError('result must be a new private checkout work file')
        if not manifest_arg:
            if mode=='Apply': raise ValueError('Apply requires exact manifest')
            entries=[];errors=[];exclusions=set(args.exclude)
            def blocked(rel):
                return any(rel==x or rel.startswith(x+'/') for x in exclusions) or any(x.lower() in {'.git','.venv','venv','node_modules','journal','data','logs','notes','charts','uploads','archives','credentials','.runtime'} for x in rel.split('/'))
            # Default root listing only. Descent requires exact, explicitly named job roots.
            stack=[(root,False)]
            for rel in args.audit_root:
                if blocked(rel) or not permitted(rel,{'path':rel}): raise ValueError('protected/unreviewed Audit root')
                stack.append((target(rel),True))
            seen=set()
            while stack:
                deadline();p,descend=stack.pop()
                if str(p) in seen: continue
                seen.add(str(p))
                try:
                    with os.scandir(p) as it:
                        for e in it:
                            rel=Path(e.path).relative_to(root).as_posix()
                            if blocked(rel):
                                entries.append(dict(path=rel,type='pruned',decision='retain',reason='protected or explicit inaccessible exclusion; contents unknown'));continue
                            q=Path(e.path);s=e.stat(follow_symlinks=False)
                            link=stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&1024
                            typ='reparse' if link else 'directory' if stat.S_ISDIR(s.st_mode) else 'file'
                            entries.append(dict(path=rel,type=typ,size=s.st_size if typ=='file' else 0,identity=identity(q),decision='retain',reason='unreviewed'))
                            if descend and typ=='directory': stack.append((q,True))
                except OSError as ex: errors.append(dict(path=str(p),error=str(ex)))
            receipt.update(entries=entries,errors=errors,root=str(root),root_identity=identity(root),schema=1);receipt['ok']=not errors
        else:
            mp=Path(os.path.abspath(manifest_arg));ancestry(mp)
            if os.path.commonpath([root,mp])!=str(root): raise ValueError('manifest outside checkout')
            plan=json.loads(mp.read_text(encoding='utf-8-sig'))
            if plan['schema']!=1 or os.path.normcase(plan['root'])!=os.path.normcase(str(root)): raise ValueError('root mismatch')
            if any(plan['root_identity'][k]!=identity(root)[k] for k in ('dev','ino')): raise ValueError('root identity changed')
            protected_receipts.add(mp)
            if result_arg: protected_receipts.add(rp);protected_receipts.add(Path(str(rp)+'.events.jsonl'))
            for key,spec in plan.get('registrations',{}).items():
                record=proof(spec);registrations[key]=record
            for spec in plan.get('external_roots',[]):
                key=spec.get('id');base=Path(os.path.abspath(spec.get('path','')))
                # Named, task-owned sibling backup roots only; never C:\GPT or a drive.
                if not key or key in roots or base.parent!=root.parent or not re.fullmatch(r'CODEX-job[0-9]+-backups',base.name): raise ValueError('arbitrary external root')
                ancestry(base)
                if any(identity(base)[k]!=spec['identity'][k] for k in ('dev','ino','attributes')): raise ValueError('external root identity changed')
                record=registrations.get(spec.get('registration'))
                if not record or record.get('root')!=str(base) or record.get('root_identity')!=spec['identity'] or record.get('task_owned') is not True or spec.get('explicitly_authorized') is not True: raise ValueError('external creation/authorization evidence absent')
                roots[key]=base;root_identities[key]=spec['identity']
            for key,spec in plan.get('synthetic_fixtures',{}).items():
                rel=spec.get('path','');parts=rel.split('/')
                if len(parts)<5 or parts[:2]!=['.codex-work','active'] or parts[3]!='fixtures': raise ValueError('fixture must be an isolated managed fixture root')
                record=registrations.get(key)
                if not record or record.get('fixture_path')!=rel or record.get('isolated_fixture') is not True or record.get('owner_finished') is not True or not record.get('creation_evidence'): raise ValueError('synthetic creation/completion proof absent')
                if any(identity(target(rel))[k]!=spec['identity'][k] for k in ('dev','ino','attributes')): raise ValueError('fixture root replaced')
                fixture_roots[key]=spec
            for authorization in plan.get('accepted_retention_release',[]):
                job=authorization.get('job',''); parts=job.split('/')
                if len(parts)!=3 or parts[:2]!=['.codex-work','retained'] or parts[2] in ('','.','..') or any(c in job for c in ('\\',':')): raise ValueError('invalid accepted job root')
                target(job)
                if job in accepted_jobs: raise ValueError('duplicate accepted job')
                if authorization.get('completed') is not True or authorization.get('accepted') is not True or authorization.get('release_condition_ended') is not True: raise ValueError('acceptance/release condition not ended')
                if not all(isinstance(authorization.get(k),str) and authorization[k].strip() for k in ('completion_evidence','acceptance_evidence','release_condition')): raise ValueError('missing acceptance evidence')
                accepted_jobs[job]=authorization
            if args.reconcile:
                reconcile(plan)
            else:
                entries=[e for e in plan['entries'] if e['decision']=='delete'];names=[e.get('root_id','checkout')+':'+e['path'].lower() for e in entries]
                if len(names)!=len(set(names)): raise ValueError('duplicate target')
                selected=set(names)
                ancestor_ids={}
                if result_arg and (rp==mp or 'checkout:'+rp.relative_to(root).as_posix().lower() in selected): raise ValueError('receipt conflicts with plan')
                for e in entries:
                    q=check(e);base=base_for(e)
                    for ancestor in q.parents:
                        if ancestor==base.parent: break
                        ancestor_ids[str(ancestor)]=identity(ancestor)
                    if q==mp: raise ValueError('manifest is a target')
                    if e['type']=='directory':
                        with os.scandir(q) as it:
                            for child in it:
                                if e.get('root_id','checkout')+':'+Path(child.path).relative_to(base).as_posix().lower() not in selected: raise ValueError('unlisted child: '+child.path)
                if any(target(e['path'],base_for(e)) in protected_receipts for e in entries): raise ValueError('plan selects current/provenance/required receipt')
                receipt['validated']=len(entries)
                if mode=='Audit': receipt['ok']=True
                else:
                    events_path=Path(str(rp)+'.events.jsonl')
                    ancestry(events_path.parent)
                    if events_path.exists() or 'checkout:'+events_path.relative_to(root).as_posix().lower() in selected: raise ValueError('event receipt conflicts with plan')
                    event_stream=events_path.open('x',encoding='utf-8')
                    event(dict(status='validated',targets=len(entries)))
                    ordered=sorted(entries,key=lambda e:(e['type']=='directory',-e['path'].count('/'),e['path']))
                    for i,e in enumerate(ordered):
                        try:
                            event(dict(path=e['path'],status='planned',identity=e['identity']))
                            q=check(e,directory_after=e['type']=='directory')
                            guarded_remove(q,e,ancestor_ids)
                            if e['type']=='file': receipt['deleted_bytes']+=e['size']
                            receipt['results'].append(dict(path=e['path'],root_id=e.get('root_id','checkout'),type=e['type'],status='deleted',size=e['size']))
                            event(receipt['results'][-1])
                        except Exception as ex:
                            receipt['results'].append(dict(path=e['path'],status='failed',error=str(ex)))
                            receipt['results'].extend(dict(path=x['path'],status='skipped',reason='batch stopped after failure') for x in ordered[i+1:])
                            raise
                    receipt['ok']=True
    except Exception as ex: receipt['error']=str(ex)
    finally:
        if event_stream is not None: event_stream.close()
    receipt['elapsed']=time.monotonic()-started
    if result_arg:
        try:
            # Revalidate the receipt parent, never follow a replacement junction.
            ancestry(rp.parent)
            if os.path.commonpath([root,rp])!=str(root) or '.codex-work' not in rp.parts: raise ValueError('unsafe receipt')
            with rp.open('x',encoding='utf-8') as f: json.dump(receipt,f,indent=2)
        except Exception as ex: receipt['ok']=False;receipt['result_error']=str(ex)
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('entries','results')} if result_arg else receipt,sort_keys=True))
    return 0 if receipt['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
