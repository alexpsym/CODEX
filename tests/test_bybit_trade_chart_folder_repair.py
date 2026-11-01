from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from tools.master_journal_workbook import TRADE_LOG_HEADERS, _TRADE_FOLDER_INDEX_CACHE
from tools import repair_bybit_trade_chart_folder as repairer


ROW_ID = repairer.EXPECTED_ROW_ID


def _fixture(tmp_path: Path):
    workbook = tmp_path / "Trading Journal.xlsx"
    json_path = tmp_path / "trading_journal.json"
    root = tmp_path / "CRYPTO"
    source = root / "2026" / "APRIL" / "C377"
    source.mkdir(parents=True)
    (source / "chart.png").write_bytes(b"original-chart-bytes")
    (source / ".capture-meta").write_bytes(b"hidden-entry")
    nested = source / "notes"
    nested.mkdir()
    (nested / "caption.txt").write_text("manual capture note", encoding="utf-8")

    wb = Workbook()
    ws = wb.active
    ws.title = "Trade Log"
    for col, header in enumerate(TRADE_LOG_HEADERS, start=1):
        ws.cell(1, col, header)
    row = {
        "Trade Number": "C377", "Open Time": datetime(2026, 10, 4, 13, 7, 22),
        "Close Time": datetime(2026, 10, 7, 12, 1, 7), "Account": "Bybit Demo",
        "Symbol": "BTCUSDT", "Side": "Buy", "Qty": 0.004, "Entry Price": 84763.8,
        "Exit Price": 84100.3, "Net P/L": -3.13515409, "Notes": "keep this note",
        "Row ID": ROW_ID,
    }
    for col, header in enumerate(TRADE_LOG_HEADERS, start=1):
        if header in row:
            ws.cell(2, col, row[header])
    number_cell = ws.cell(2, 1)
    number_cell.hyperlink = source.resolve().as_uri()
    number_cell.style = "Hyperlink"
    ws.cell(2, 45).font = Font(bold=True, color="123456")
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{ws.cell(2, len(TRADE_LOG_HEADERS)).coordinate}"
    presentation = wb.create_sheet("Presentation")
    presentation["A1"] = "Authored label"
    presentation["A2"] = "manual link"
    presentation["A2"].hyperlink = "https://example.invalid/manual"
    presentation["B2"] = "=1+1"
    presentation.merge_cells("A4:B4")
    presentation["A4"] = "merged authored cell"
    wb.save(workbook)
    json_path.write_text(json.dumps({"items": [{
        "id": ROW_ID, "trade_number": "C377", "account": "Bybit Demo", "symbol": "BTCUSDT",
        "open_time": "2026-10-04T13:07:22+10:00", "close_time": "2026-10-07T12:01:07+10:00",
        "qty": 0.004, "entry_price": 84763.8, "exit_price": 84100.3, "net_profit": -3.13515409,
    }]}), encoding="utf-8")
    return workbook, json_path, root, source


def _workbook_snapshot(path: Path):
    wb = load_workbook(path, data_only=False, read_only=False)
    try:
        trade = wb["Trade Log"]
        row = next(i for i in range(2, trade.max_row + 1) if trade.cell(i, 50).value == ROW_ID)
        return {
            "number": trade.cell(row, 1).value,
            "hyperlink": trade.cell(row, 1).hyperlink.target,
            "style": trade.cell(row, 1).style_id,
            "note": trade.cell(row, 45).value,
            "note_font": trade.cell(row, 45).font.bold,
            "open": trade.cell(row, 2).value,
            "close": trade.cell(row, 3).value,
            "formula": wb["Presentation"]["B2"].value,
            "manual_link": wb["Presentation"]["A2"].hyperlink.target,
            "merged": tuple(str(x) for x in wb["Presentation"].merged_cells.ranges),
            "freeze": trade.freeze_panes,
            "filter": trade.auto_filter.ref,
        }
    finally:
        wb.close()


def test_bybit_chart_folder_repair_moves_and_retargets_once_then_is_verified_noop(tmp_path, monkeypatch):
    workbook, json_path, root, source = _fixture(tmp_path)
    recovery = tmp_path / "recovery"
    monkeypatch.setattr(
        repairer,
        "_select_trade_chart_root",
        lambda prefix: (root, {"selection": "fixture_selected_root", "root": str(root.resolve())}),
    )
    before = _workbook_snapshot(workbook)
    plan = repairer.repair(workbook, json_path, recovery_root=recovery)
    assert plan["applied"] is False
    assert plan["plan"]["action"] == "move_folder_and_retarget_existing_link"
    assert len(plan["plan"]["inventory"]) == 4
    assert source.is_dir()
    assert not (root / "2026" / "OCTOBER" / "C377").exists()

    # A stale, unlocked Excel owner marker must not block the verified repair
    # and is preserved as user-owned temporary metadata.
    owner_file = workbook.with_name("~$" + workbook.name)
    owner_file.write_bytes(b"stale but unlocked owner marker")

    applied = repairer.repair(
        workbook, json_path, recovery_root=recovery,
        apply=True, confirm_plan_sha256=plan["plan_sha256"],
    )
    destination = root / "2026" / "OCTOBER" / "C377"
    assert applied["applied"] is True
    assert applied["verified_workbook"]["row_id"] == ROW_ID
    assert applied["verified_workbook"]["trade_number"] == "C377"
    assert applied["verified_workbook"]["open_time"] == "2026-10-04T13:07:22"
    assert applied["verified_workbook"]["close_time"] == "2026-10-07T12:01:07"
    assert applied["verified_workbook"]["hyperlink"] == destination.resolve().as_uri()
    assert not source.exists()
    assert repairer._inventory(destination) == plan["plan"]["inventory"]
    assert Path(applied["workbook_backup"]).is_file()
    assert Path(applied["chart_folder_backup"]).is_dir()
    assert owner_file.read_bytes() == b"stale but unlocked owner marker"
    after = _workbook_snapshot(workbook)
    assert after == {**before, "hyperlink": destination.resolve().as_uri()}
    assert _TRADE_FOLDER_INDEX_CACHE == {}

    digest_after_move = repairer._sha256(workbook)
    repeat_plan = repairer.repair(workbook, json_path, recovery_root=recovery)
    assert repeat_plan["plan"]["action"] == "verified_no_op"
    repeated = repairer.repair(
        workbook, json_path, recovery_root=recovery,
        apply=True, confirm_plan_sha256=repeat_plan["plan_sha256"],
    )
    assert repeated["verified_no_op"] is True
    assert repeated["verified_workbook"]["hyperlink"] == destination.resolve().as_uri()
    assert repairer._sha256(workbook) == digest_after_move
    assert len(list(recovery.iterdir())) == 1


def test_bybit_chart_folder_destination_conflict_refuses_without_changes(tmp_path):
    workbook, json_path, root, source = _fixture(tmp_path)
    destination = root / "2026" / "OCTOBER" / "C377"
    destination.mkdir(parents=True)
    (destination / "user-file.png").write_bytes(b"do-not-overwrite")
    before_workbook = repairer._sha256(workbook)
    before_source = repairer._inventory(source)
    before_destination = repairer._inventory(destination)
    with pytest.raises(repairer.RepairRefused, match="Both APRIL/C377 and OCTOBER/C377"):
        repairer.repair(workbook, json_path, chart_root=root, recovery_root=tmp_path / "recovery", apply=True, confirm_plan_sha256="unused")
    assert repairer._sha256(workbook) == before_workbook
    assert repairer._inventory(source) == before_source
    assert repairer._inventory(destination) == before_destination
    assert not (tmp_path / "recovery").exists()


def test_bybit_chart_folder_repair_failure_restores_workbook_and_folder(tmp_path, monkeypatch):
    workbook, json_path, root, source = _fixture(tmp_path)
    recovery = tmp_path / "recovery"
    plan = repairer.repair(workbook, json_path, chart_root=root, recovery_root=recovery)
    before_workbook = repairer._sha256(workbook)
    before_inventory = repairer._inventory(source)

    def fail_verification(_workbook, _folder):
        raise repairer.RepairRefused("injected final-verification failure")

    monkeypatch.setattr(repairer, "_verify_saved_workbook", fail_verification)
    with pytest.raises(repairer.RepairRefused, match='"complete": true'):
        repairer.repair(
            workbook, json_path, chart_root=root, recovery_root=recovery,
            apply=True, confirm_plan_sha256=plan["plan_sha256"],
        )
    assert repairer._sha256(workbook) == before_workbook
    assert repairer._inventory(source) == before_inventory
    assert not (root / "2026" / "OCTOBER" / "C377").exists()


def test_crypto_number_repair_chronology_anchors_and_refresh(tmp_path, monkeypatch):
    from tools import master_journal_workbook as m
    boundary=dict(id='lumia',symbol='LUMIAUSDT',account='Bybit Demo',asset_class='crypto',open_time='2026-10-11T14:27:38',trade_number='C378')
    def trade(rid,symbol,time,number,test=False):
        return dict(id=rid,symbol=symbol,account='Bybit Demo',asset_class='crypto',source='master_journal',row_type='trade',open_time=time,trade_number=number,is_test_trade=test)
    ids=['bybit:demo:trade:'+s+':'+str(i)*16 for i,s in enumerate(['BTCUSDT','QUSDT','QUSDT','TREEUSDT','WETUSDT','DOODUSDT'],1)]
    rows=[trade(ids[0],'BTCUSDT','2026-10-18T09:21:51','C379'),trade(ids[1],'QUSDT','2026-10-18T20:38:05','C379'),trade(ids[2],'QUSDT','2026-10-19T17:05:12','C380'),trade(ids[3],'TREEUSDT','2026-10-24T20:48:54','C382'),trade(ids[4],'WETUSDT','2026-10-24T21:16:07','C383'),trade(ids[5],'DOODUSDT','2026-10-27T17:22:33','C385')]
    excluded=trade('test','BTCUSDT','2026-10-18T09:48:41',None,True)
    forex=dict(id='fx',symbol='EURUSD',account='OANDA',asset_class='forex',open_time='2026-10-20T00:00:00',trade_number='F42')
    source=[*reversed(rows),excluded,forex,boundary]
    before=json.loads(json.dumps(source));anchors={ids[0]:'C379',ids[1]:'C380',ids[2]:'C381',ids[5]:'C384'}
    plan=m.plan_crypto_number_correction(source,'lumia',anchors=anchors,allocations={})
    assert [e['id'] for e in plan['edits']]==ids
    assert [e['new_number'] for e in plan['edits']]==['C379','C380','C381','C382','C383','C384']
    assert not plan['conflicts'] and source==before
    pending={'eth':dict(scope=['bybit','demo','','linear','ETHUSDT'],identities=['order:unrelated'],owner='eth-owner',trade_number='C384')}
    assert m.plan_crypto_number_correction(source,'lumia',anchors=anchors,allocations=pending)['conflicts'][0]['allocation']=='eth'
    assert pending['eth']['trade_number']=='C384'
    with pytest.raises(ValueError,match='anchors'):
        m.plan_crypto_number_correction(source,'lumia',anchors={ids[0]:'C380'},allocations={})
    # Exercise the real existing allocator with a canonical workbook Row ID alias.
    monkeypatch.setattr(m,'REPO_ROOT',tmp_path)
    crypto=tmp_path/'CRYPTO';fx=tmp_path/'FOREX';crypto.mkdir();fx.mkdir()
    row=rows[1].copy();row['trade_number']='C380'
    scope,aliases=m._trade_folder_identity(row)
    assert scope==('bybit','demo','','linear','QUSDT') and aliases=={'row:'+ids[1]}
    folder=crypto/'2026'/'OCTOBER'/'C380';folder.mkdir(parents=True);(folder/'.ltt-trade-owner.json').write_text(json.dumps('owner'))
    state={'q':dict(scope=list(scope),identities=sorted(aliases),row_id='accepted:old',trade_number='C380',folder=str(folder),root=str(crypto),owner='owner',status='ready'),
           'unrelated':dict(scope=['bybit','demo','','linear','BTWUSDT'],identities=['order:unrelated'],row_id='accepted:unrelated',trade_number='C386',folder=str(crypto/'2026/OCTOBER/C386'),root=str(crypto),owner='other',status='reserved')}
    state['retired']=dict(scope=['bybit','demo','','linear','ETHUSDT'],identities=['order:cancelled-eth'],row_id='accepted:eth',trade_number=None,folder=None,root=str(crypto),owner='eth-owner',status='retired_cancelled',retired_from={'trade_number':'C384'})
    allocation=tmp_path/'render/data/trade_folder_allocations.json';allocation.parent.mkdir(parents=True);allocation.write_text(json.dumps(state))
    first=m.assign_trade_numbers_and_create_folders([row],crypto_root=crypto,forex_root=fx)
    assert first['preserved_existing']==1 and row['trade_number']=='C380' and row['trade_folder']==str(folder)
    assert len(json.loads(allocation.read_text()))==3
    second=m.assign_trade_numbers_and_create_folders([row],crypto_root=crypto,forex_root=fx)
    assert second['preserved_existing']==1 and len(json.loads(allocation.read_text()))==3
    future=dict(id='future',broker='bybit',account='demo',symbol='NEWUSDT',order_id='future-order',open_time='2026-11-01T10:00:00',asset_class='crypto')
    m.assign_trade_numbers_and_create_folders([future],crypto_root=crypto,forex_root=fx)
    assert future['trade_number']=='C387'
    assert json.loads(allocation.read_text())['unrelated']==state['unrelated']
    retired=dict(id='accepted:eth',broker='bybit',account='demo',symbol='ETHUSDT',order_id='cancelled-eth',open_time='2026-10-25T12:00:00')
    m.assign_trade_numbers_and_create_folders([retired],crypto_root=crypto,forex_root=fx)
    assert 'trade_number' not in retired and not (crypto/'2026/OCTOBER/C384').exists()
    assert m.early_trade_folder_rows([{'early_trade_folder_request':{'order_id':'cancelled-eth','retired_number_reservation':{'reason':'cancelled'}}}])==[]


def test_crypto_number_repair_existing_links_shared_relationship_and_package(tmp_path):
    import re,zipfile,io
    from copy import copy
    import xml.etree.ElementTree as E
    from tools import master_journal_workbook as m
    source=tmp_path/'source.xlsx';candidate=tmp_path/'candidate.xlsx'
    wb=Workbook();ws=wb.active;ws.title='Trade Log'
    ws['A4']='C379';ws['B4']='stable-q';ws['C4']='=1+2';ws['D4']='Counter Trend'
    ws['A5']='C12';ws['B5']='unrelated';ws['A4'].hyperlink='file:///old/folder';ws['A5'].hyperlink='file:///old/folder'
    ws['A4'].font=Font(name='Verdana',bold=True);ws.row_dimensions[4].height=31;ws.freeze_panes='E4'
    wb.create_sheet('Other')['A1']='=2+2';wb.save(source);wb.close()
    with zipfile.ZipFile(source) as z:infos=z.infolist();parts={i.filename:z.read(i.filename) for i in infos}
    part='xl/worksheets/sheet1.xml';rp='xl/worksheets/_rels/sheet1.xml.rels';payload=parts[part].decode()
    links=re.findall(r'<hyperlink\b[^>]*/>',payload);assert len(links)==2
    rid=re.search(r'r:id="([^"]+)"',links[0])[1]
    shared=re.sub(r'r:id="[^"]+"','r:id="'+rid+'"',links[1]);payload=payload.replace(links[1],shared)
    payload,count=re.subn(r'(<c\b[^>]*\br="C4"[^>]*>.*?<f>1\+2</f>)<v(?:\s*/>|>\s*</v>)',r'\1<v>3</v>',payload,flags=re.S);assert count==1;parts[part]=payload.encode()
    with zipfile.ZipFile(source,'w') as z:
        for info in infos:z.writestr(copy(info),parts[info.filename])
    original=source.read_bytes();updates={'A4':dict(id='stable-q',id_cell='B4',old_number='C379',new_number='C380',old_link='file:///old/folder',new_link='file:///correct/Q-first')}
    result=m.patch_existing_trade_numbers_and_links(source,candidate,updates)
    assert result['changed_cells']==['A4'] and source.read_bytes()==original
    assert m.audit_existing_trade_number_package(original,candidate.read_bytes(),updates)
    with zipfile.ZipFile(candidate) as z:
        tree=E.fromstring(z.read(part));ns={'s':m._SPREADSHEET_NAMESPACE} if hasattr(m,'_SPREADSHEET_NAMESPACE') else {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        output=z.read(part).decode();assert '<f>1+2</f><v>3</v>' in output
        links=tree.findall('s:hyperlinks/s:hyperlink',ns);assert len(links)==2
        rn='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
        ids={h.get('ref'):h.get(rn+'id') for h in links};assert ids['A4']!=ids['A5']
        rels={x.get('Id'):x.get('Target') for x in E.fromstring(z.read(rp))}
        assert rels[ids['A4']]=='file:///correct/Q-first' and rels[ids['A5']]=='file:///old/folder'
    bad=dict(updates);bad['A4']={**updates['A4'],'id':'wrong-execution'}
    with pytest.raises(ValueError,match='identity'):
        m.patch_existing_trade_numbers_and_links(source,tmp_path/'refused.xlsx',bad)


def test_crypto_number_repair_coordinated_transaction_rollback_and_collisions(tmp_path, monkeypatch):
    import sqlite3
    from tools import master_journal_workbook as m
    def prepared(name):
        base=tmp_path/name;base.mkdir();month=base/'CRYPTO/2026/OCTOBER';month.mkdir(parents=True)
        owners={'C379':'q1-owner','C380':'q2-owner','C381':'q2-owner','C384':'eth-owner','C385':'dood-owner'}
        for number,owner in owners.items():
            folder=month/number;folder.mkdir();(folder/'.ltt-trade-owner.json').write_text(json.dumps(owner))
        (month/'C379/chart.png').write_bytes(b'first chart');(month/'C380/notes').mkdir();(month/'C380/notes/manual.txt').write_text('unique manual note')
        (month/'C385/chart.png').write_bytes(b'dood chart')
        workbook=base/'journal.xlsx';candidate=base/'candidate.xlsx';wb=Workbook();ws=wb.active;ws.title='Trade Log'
        rows=[dict(id=rid,trade_number=number,financial=100+i,is_test_trade=False) for i,(rid,number) in enumerate([('btc','C379'),('q1','C379'),('q2','C380'),('dood','C385')])]
        updates={}
        for i,row in enumerate(rows,4):
            ws.cell(i,1,row['trade_number']);ws.cell(i,2,row['id']);ws.cell(i,3,'=1+2');ws.cell(i,1).hyperlink=(month/row['trade_number']).as_uri()
            number={'q1':'C380','q2':'C381','dood':'C384'}.get(row['id'])
            if number:updates['A'+str(i)]=dict(id=row['id'],id_cell='B'+str(i),old_number=row['trade_number'],new_number=number,old_link=(month/row['trade_number']).as_uri(),new_link=(month/number).as_uri())
        wb.save(workbook);wb.close();m.patch_existing_trade_numbers_and_links(workbook,candidate,updates)
        journal=base/'runtime.json';journal.write_text(json.dumps(dict(items=rows,unrelated={'value':42})))
        allocation=base/'render/data/trade_folder_allocations.json';allocation.parent.mkdir(parents=True);allocation.write_text(json.dumps({'eth':dict(trade_number='C384',folder=str(month/'C384'),status='ready',owner='eth-owner'),'dood':dict(trade_number='C385',folder=str(month/'C385'),status='ready',owner='dood-owner')}))
        contexts=base/'contexts.json';contexts.write_text(json.dumps([dict(order_id='eth-order',is_test_trade=False,early_trade_folder_request={'order_id':'eth-order'})]))
        stores=[]
        def changes(path,edits):
            doc=json.loads(path.read_text());out=[]
            for pointer,after in edits:
                target,key=repairer._number_pointer(doc,pointer);out.append(dict(pointer=pointer,before_exists=key in target,before=target.get(key),after=after))
            stores.append(dict(path=str(path),sha256=repairer._sha256(path),changes=out))
        changes(journal,[( ['items',{'id':rid},field],value) for rid,num in [('btc','C379'),('q1','C380'),('q2','C381'),('dood','C384')] for field,value in [('trade_number',num),('trade_folder',str(month/num))]])
        changes(allocation,[(['eth','trade_number'],None),(['eth','folder'],None),(['eth','status'],'retired_cancelled'),(['eth','retired_from'],{'trade_number':'C384','folder':str(month/'C384')}),(['dood','trade_number'],'C384'),(['dood','folder'],str(month/'C384'))])
        changes(contexts,[([0,'early_trade_folder_request','retired_number_reservation'],{'reason':'user confirmed cancellation','owner':'eth-owner'})])
        db=base/'store.sqlite';sql=[]
        with sqlite3.connect(db) as con:
            con.execute('CREATE TABLE journal_trades(id TEXT PRIMARY KEY,payload_json TEXT,imported_at TEXT)')
            for row in rows:
                raw=json.dumps(row);con.execute('INSERT INTO journal_trades VALUES(?,?,?)',(row['id'],raw,'unchanged'))
                num={'btc':'C379','q1':'C380','q2':'C381','dood':'C384'}[row['id']]
                sql.append(dict(id=row['id'],before=raw,imported_at='unchanged',fields={'trade_number':num,'trade_folder':str(month/num)}))
        moves=[dict(source=str(month/a),destination=str(month/b),inventory=repairer._number_inventory(month/a)) for a,b in [('C379','C380'),('C380','C381'),('C385','C384')]]
        plan=dict(conflicts=[],workbook_path=str(workbook),workbook_sha256=repairer._sha256(workbook),candidate_path=str(candidate),candidate_sha256=repairer._sha256(candidate),package_updates=updates,recovery_root=str(base/'recovery'),chart_root=str(base/'CRYPTO'),json_stores=stores,sqlite_path=str(db),sql_records=sql,folder_moves=moves,obsolete_scaffolds=[dict(path=str(month/x),inventory=repairer._number_inventory(month/x)) for x in ('C381','C384')],folder_creates=[dict(path=str(month/'C379'),owner='btc-owner')])
        baseline=dict(book=workbook.read_bytes(),stores={s['path']:json.loads(Path(s['path']).read_text()) for s in stores},folders={str(month/x):repairer._number_inventory(month/x) for x in owners})
        return base,plan,baseline
    def run(base,plan,failpoint=None):
        monkeypatch.setattr(m,'REPO_ROOT',base)
        return repairer.apply_number_repair_plan(plan,gate=lambda:repairer._number_repair_gate(Path(plan['workbook_path']),lock_path=base/'journal.lock'),failpoint=failpoint)
    # Reject genuine user-content collisions before any production-like mutation.
    base,plan,baseline=prepared('collision');(base/'CRYPTO/2026/OCTOBER/C381/new-user-note').write_text('keep')
    with pytest.raises(repairer.RepairRefused,match='scaffold'):
        run(base,plan)
    assert Path(plan['workbook_path']).read_bytes()==baseline['book'] and not (base/'recovery/apply.claim').exists()
    for phase in ('folder_staged','sql_committed'):
        base,plan,baseline=prepared('rollback-'+phase)
        def failure(event):
            if event==phase:raise RuntimeError('injected '+phase)
        with pytest.raises(repairer.RepairRefused,match='"rollback_complete": true'):
            run(base,plan,failure)
        assert Path(plan['workbook_path']).read_bytes()==baseline['book']
        for path,document in baseline['stores'].items():assert json.loads(Path(path).read_text())==document
        for path,inventory in baseline['folders'].items():assert repairer._number_inventory(Path(path))==inventory
        with sqlite3.connect(plan['sqlite_path']) as con:
            for record in plan['sql_records']:assert con.execute('SELECT payload_json,imported_at FROM journal_trades WHERE id=?',(record['id'],)).fetchone()==(record['before'],'unchanged')
    base,plan,baseline=prepared('success');result=run(base,plan)
    assert result['ok'] and result['phase']=='committed' and result['folders_moved']==3 and result['scaffolds_removed']==2
    assert Path(plan['workbook_path']).read_bytes()==Path(plan['candidate_path']).read_bytes()
    for move in plan['folder_moves']:assert repairer._number_inventory(Path(move['destination']))==move['inventory']
    state=json.loads((base/'render/data/trade_folder_allocations.json').read_text());assert state['eth']['status']=='retired_cancelled' and state['eth']['trade_number'] is None and state['dood']['trade_number']=='C384'
    with sqlite3.connect(plan['sqlite_path']) as con:
        for record in plan['sql_records']:
            value=json.loads(con.execute('SELECT payload_json FROM journal_trades WHERE id=?',(record['id'],)).fetchone()[0]);assert value['financial']==json.loads(record['before'])['financial'] and value['trade_number']==record['fields']['trade_number']
    assert run(base,plan)['noop'] is True
    assert (base/'recovery/original.xlsx').stat().st_size<25*1024**2
