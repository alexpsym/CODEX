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
    import json, os, stat, sys, time
    from pathlib import Path, PurePosixPath
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', '-RepoRoot', default=os.getcwd())
    parser.add_argument('--mode', '-Mode', choices=('Audit','Apply'), default='Audit')
    parser.add_argument('--manifest', '-Manifest', default='')
    parser.add_argument('--result', '-Result', default='')
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
    def target(rel):
        if not isinstance(rel,str) or chr(92) in rel or ':' in rel: raise ValueError('noncanonical relative path')
        parts=PurePosixPath(rel).parts
        if not parts or rel!='/'.join(parts) or any(x in ('.','..','') for x in parts) or rel.startswith('/'): raise ValueError('escaping/noncanonical path: '+rel)
        q=root.joinpath(*parts)
        if os.path.commonpath([root,q])!=str(root) or q==root: raise ValueError('outside root')
        ancestry(q)
        return q
    def permitted(rel):
        parts=rel.lower().split('/')
        if any(x in {'.git','.venv','venv','node_modules','.runtime','uploads','archives','credentials','notes','charts','raw','history','data'} or x.startswith('.env') or x.endswith(('.pem','.key')) for x in parts): return False
        if parts[0] in {'journal','page-video-downloader'}: return False
        if parts[-1].endswith(('.lock','-wal','-shm','-journal')) or parts[-1] in {'settings.json','credentials.json','state.json'}: return False
        if parts[0] in {'render','bybit_monitor','oanda_monitor'}:
            return '__pycache__' in parts and (parts[-1]=='__pycache__' or parts[-1].endswith('.pyc'))
        if '__pycache__' in parts: return parts[-1]=='__pycache__' or parts[-1].endswith('.pyc')
        if parts[0]=='.codex-work': return len(parts)>=3 and parts[1]=='active'
        return parts[0].startswith(('.job','.pytest','.codex_tmp','.counter_trend_repair_'))
    def check(e,directory_after=False):
        deadline()
        if any(identity(root)[k]!=root_expected[k] for k in ('dev','ino','attributes')): raise ValueError('root identity changed')
        q=target(e['path'])
        if not permitted(e['path']): raise ValueError('protected path: '+e['path'])
        s=q.lstat();typ=e['type']
        if typ not in ('file','directory') or (typ=='file' and not stat.S_ISREG(s.st_mode)) or (typ=='directory' and not stat.S_ISDIR(s.st_mode)): raise ValueError('unexpected type')
        now=identity(q);expected=e['identity'];keys=('dev','ino','attributes') if directory_after else tuple(now)
        if any(now[k]!=expected[k] for k in keys): raise ValueError('identity/metadata changed: '+e['path'])
        if typ=='file' and now['size']!=e['size']: raise ValueError('size changed')
        if not all(isinstance(e.get(k),str) and e[k].strip() for k in ('evidence','status','reason')): raise ValueError('missing review evidence')
        return q
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
            h=create(str(path),0x80 | (0x10000 if delete else 0),3 if not delete else 1,None,3,0x02200000,None)
            if h==wintypes.HANDLE(-1).value: raise ctypes.WinError(ctypes.get_last_error())
            return h
        with ExitStack() as stack:
            # Lock root outward-to-inward. Share READ/WRITE but not DELETE for parents.
            parents=[]
            for ancestor in q.parents:
                if ancestor==root.parent: break
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
            check(e,directory_after=e['type']=='directory')
            if e['type']=='directory':
                with os.scandir(q) as it:
                    if next(it,None) is not None: raise ValueError('directory is not empty')
            disposition=Disposition(1)
            if not dispose(h,4,ctypes.byref(disposition),ctypes.sizeof(disposition)): raise ctypes.WinError(ctypes.get_last_error())
        # Closing the verified DELETE handle completes that individual deletion.
        if q.exists(): raise OSError('deletion did not complete')


    try:
        root=Path(os.path.abspath(root_arg));ancestry(root);root_expected=identity(root)
        if mode=='Apply' and not result_arg: raise ValueError('Apply requires a new private Result receipt')
        if not (root/'.git').is_dir() or not (root/'AGENTS.md').is_file(): raise ValueError('not a checkout root')
        ancestry(root/'.git');ancestry(root/'AGENTS.md')
        if result_arg:
            rp=Path(os.path.abspath(result_arg));ancestry(rp.parent)
            if os.path.commonpath([root,rp])!=str(root) or '.codex-work' not in rp.parts or rp.exists(): raise ValueError('result must be a new private checkout work file')
        if not manifest_arg:
            if mode=='Apply': raise ValueError('Apply requires exact manifest')
            stack=[root];entries=[];errors=[]
            while stack:
                deadline();p=stack.pop()
                try:
                    with os.scandir(p) as it:
                        for e in it:
                            q=Path(e.path);rel=q.relative_to(root).as_posix();s=e.stat(follow_symlinks=False)
                            link=stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&1024
                            typ='reparse' if link else 'directory' if stat.S_ISDIR(s.st_mode) else 'file'
                            entries.append(dict(path=rel,type=typ,size=s.st_size if typ=='file' else 0,identity=identity(q),decision='retain',reason='unreviewed'))
                            if typ=='directory' and rel!='.git': stack.append(q)
                except OSError as ex: errors.append(dict(path=str(p),error=str(ex)))
            receipt.update(entries=entries,errors=errors,root=str(root),root_identity=identity(root),schema=1);receipt['ok']=not errors
        else:
            mp=Path(os.path.abspath(manifest_arg));ancestry(mp)
            if os.path.commonpath([root,mp])!=str(root): raise ValueError('manifest outside checkout')
            plan=json.loads(mp.read_text(encoding='utf-8-sig'))
            if plan['schema']!=1 or os.path.normcase(plan['root'])!=os.path.normcase(str(root)): raise ValueError('root mismatch')
            if any(plan['root_identity'][k]!=identity(root)[k] for k in ('dev','ino')): raise ValueError('root identity changed')
            entries=[e for e in plan['entries'] if e['decision']=='delete'];names=[e['path'].lower() for e in entries]
            if len(names)!=len(set(names)): raise ValueError('duplicate target')
            selected=set(names)
            ancestor_ids={}
            if result_arg and (rp==mp or rp.relative_to(root).as_posix().lower() in selected): raise ValueError('receipt conflicts with plan')
            for e in entries:
                q=check(e)
                for ancestor in q.parents:
                    if ancestor==root.parent: break
                    ancestor_ids[str(ancestor)]=identity(ancestor)
                if q==mp: raise ValueError('manifest is a target')
                if e['type']=='directory':
                    with os.scandir(q) as it:
                        for child in it:
                            if Path(child.path).relative_to(root).as_posix().lower() not in selected: raise ValueError('unlisted child: '+child.path)
            receipt['validated']=len(entries)
            if mode=='Audit': receipt['ok']=True
            else:
                events_path=Path(str(rp)+'.events.jsonl')
                ancestry(events_path.parent)
                if events_path.exists() or events_path.relative_to(root).as_posix().lower() in selected: raise ValueError('event receipt conflicts with plan')
                event_stream=events_path.open('x',encoding='utf-8')
                event(dict(status='validated',targets=len(entries)))
                ordered=sorted(entries,key=lambda e:(e['type']=='directory',-e['path'].count('/'),e['path']))
                for i,e in enumerate(ordered):
                    try:
                        event(dict(path=e['path'],status='planned',identity=e['identity']))
                        q=check(e,directory_after=e['type']=='directory')
                        guarded_remove(q,e,ancestor_ids)
                        if e['type']=='file': receipt['deleted_bytes']+=e['size']
                        receipt['results'].append(dict(path=e['path'],type=e['type'],status='deleted',size=e['size']))
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
