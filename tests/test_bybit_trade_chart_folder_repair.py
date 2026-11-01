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
    boundary=dict(id='lumia',symbol='LUMIAUSDT',account='Bybit Demo',open_time='2026-10-11T14:27:38',trade_number='C378')
    def row(rid,symbol,opened,number,test=False):return dict(id=rid,symbol=symbol,account='Bybit Demo',source='master_journal',asset_class='crypto',open_time=opened,trade_number=number,is_test_trade=test)
    btc=row('bybit:demo:trade:BTCUSDT:1111','BTCUSDT','2026-10-18T09:21:51','C379',True)
    later=row('later-test','BTCUSDT','2026-10-18T09:48:41',None,True)
    genuine=[row('q1','QUSDT','2026-10-18T20:38:05','C380'),row('q2','QUSDT','2026-10-19T17:05:12','C381'),row('tree','TREEUSDT','2026-10-24T20:48:54','C382'),row('wet','WETUSDT','2026-10-24T21:16:07','C383'),row('dood','DOODUSDT','2026-10-27T17:22:33','C384')]
    live=row('accepted-live','BTWUSDT','2026-10-31T22:51:42','C386');live.update(broker='bybit',account='demo',order_id='live-order')
    fx=dict(id='fx',symbol='EURUSD',account='OANDA',open_time='2026-10-20T00:00:00',trade_number='F42')
    rows=[fx,*reversed(genuine),later,btc,boundary];before=json.loads(json.dumps(rows))
    anchors={r['id']:'C'+str(379+i) for i,r in enumerate(genuine+[live])}
    plan=m.plan_crypto_number_correction(rows,'lumia',anchors=anchors,allocations={},reservations=[live],test_row_id=btc['id'])
    assert [(e['id'],e['new_number']) for e in plan['edits']]==[(btc['id'],None),*[(r['id'],anchors[r['id']]) for r in genuine+[live]]]
    assert plan['edits'][-1]['symbol']=='BTWUSDT' and plan['edits'][-1]['kind']=='reservation'
    assert rows==before and not plan['conflicts']
    with pytest.raises(ValueError,match='anchors'):m.plan_crypto_number_correction(rows,'lumia',anchors={'q1':'C380'},allocations={},reservations=[live],test_row_id=btc['id'])
    monkeypatch.setattr(m,'REPO_ROOT',tmp_path);crypto=tmp_path/'CRYPTO';forex=tmp_path/'FOREX';crypto.mkdir();forex.mkdir()
    folder=crypto/'2026/OCTOBER/C384';folder.mkdir(parents=True);(folder/'.ltt-trade-owner.json').write_text(json.dumps('live-owner'))
    scope,aliases=m._trade_folder_identity(live)
    state={'live':dict(scope=list(scope),identities=sorted(aliases),row_id=live['id'],trade_number='C384',folder=str(folder),root=str(crypto),owner='live-owner',status='ready'),
           'btc':dict(scope=['bybit','demo','','linear','BTCUSDT'],identities=['order:btc-order','row:'+btc['id']],row_id='accepted-btc',trade_number=None,folder=None,root=str(crypto),owner='btc-owner',status='retired_test',retired_from={'trade_number':'C379'}),
           'eth':dict(scope=['bybit','demo','','linear','ETHUSDT'],identities=['order:eth-order'],row_id='accepted-eth',trade_number=None,folder=None,root=str(crypto),owner='eth-owner',status='retired_cancelled')}
    allocation=tmp_path/'render/data/trade_folder_allocations.json';allocation.parent.mkdir(parents=True);allocation.write_text(json.dumps(state))
    raw_live=dict(id='future-real-import',broker='bybit',account='Bybit Demo',symbol='BTWUSDT',raw_refs={'order_ids':['live-order']},open_time='2026-10-31T22:51:43')
    m.assign_trade_numbers_and_create_folders([raw_live],crypto_root=crypto,forex_root=forex)
    assert raw_live['trade_number']=='C384' and raw_live['trade_folder']==str(folder) and len(json.loads(allocation.read_text()))==3
    # A stale raw No cannot recreate this retired BTC reservation or consume C379.
    raw_btc=dict(id=btc['id'],source='master_journal',account='Bybit Demo',symbol='BTCUSDT',open_time=btc['open_time'],is_test_trade=False)
    m.assign_trade_numbers_and_create_folders([raw_btc],crypto_root=crypto,forex_root=forex)
    assert 'trade_number' not in raw_btc and not (crypto/'2026/OCTOBER/C379').exists()
    assert m.early_trade_folder_rows([{'early_trade_folder_request':{'order_id':'btc-order','retired_number_reservation':{'reason':'Test'}}}])==[]
    future=dict(id='new-trade',broker='bybit',account='demo',symbol='NEWUSDT',order_id='new-order',open_time='2026-11-02T12:00:00')
    m.assign_trade_numbers_and_create_folders([future],crypto_root=crypto,forex_root=forex)
    assert future['trade_number']=='C385' and json.loads(allocation.read_text())['eth']==state['eth']


def test_crypto_number_repair_existing_links_shared_relationship_and_package(tmp_path):
    import re,zipfile,io
    from copy import copy
    import xml.etree.ElementTree as E
    from tools import master_journal_workbook as m
    source=tmp_path/'source.xlsx';candidate=tmp_path/'candidate.xlsx'
    wb=Workbook();ws=wb.active;ws.title='Trade Log'
    for col,value in enumerate(['Trade Number','Row ID','Formula','Test'],1):ws.cell(1,col,value)
    ws['A4']='C379';ws['B4']='btc';ws['C4']='=1+2';ws['D4']='Yes'
    ws['A5']='C380';ws['B5']='q1';ws['D5']='No';ws['A6']='C12';ws['B6']='unrelated';ws['D6']='No'
    for coord in ('A4','A5','A6'):ws[coord].hyperlink='file:///old/shared'
    ws['A4'].font=Font(name='Verdana',bold=True);ws.row_dimensions[4].height=31;ws.freeze_panes='E4';wb.create_sheet('Other')['A1']='=2+2';wb.save(source);wb.close()
    with zipfile.ZipFile(source) as z:infos=z.infolist();parts={i.filename:z.read(i.filename) for i in infos}
    part='xl/worksheets/sheet1.xml';rp='xl/worksheets/_rels/sheet1.xml.rels';payload=parts[part].decode();links=re.findall(r'<hyperlink\b[^>]*/>',payload);rid=re.search(r'r:id="([^"]+)"',links[0])[1]
    for link in links[1:]:payload=payload.replace(link,re.sub(r'r:id="[^"]+"','r:id="'+rid+'"',link))
    payload,count=re.subn(r'(<c\b[^>]*\br="C4"[^>]*>.*?<f>1\+2</f>)<v(?:\s*/>|>\s*</v>)',r'\1<v>3</v>',payload,flags=re.S);assert count==1;parts[part]=payload.encode()
    with zipfile.ZipFile(source,'w') as z:
        for info in infos:z.writestr(copy(info),parts[info.filename])
    original=source.read_bytes();updates={'A4':dict(id='btc',id_cell='B4',old_number='C379',new_number=None,old_link='file:///old/shared',new_link=None),'A5':dict(id='q1',id_cell='B5',old_number='C380',new_number='C379',old_link='file:///old/shared',new_link='file:///final/C379')}
    m.patch_existing_trade_numbers_and_links(source,candidate,updates)
    assert source.read_bytes()==original and m.audit_existing_trade_number_package(original,candidate.read_bytes(),updates)
    with zipfile.ZipFile(candidate) as z:
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'};tree=E.fromstring(z.read(part));cells={c.get('r'):c for c in tree.findall('.//s:c',ns)}
        assert not list(cells['A4']) and 'Yes' in ''.join(cells['D4'].itertext())
        assert '<f>1+2</f><v>3</v>' in z.read(part).decode()
        links=tree.findall('s:hyperlinks/s:hyperlink',ns);assert {h.get('ref') for h in links}=={'A5','A6'}
        rn='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}';byref={h.get('ref'):h.get(rn+'id') for h in links};rels={r.get('Id'):r.get('Target') for r in E.fromstring(z.read(rp))}
        assert rels[byref['A5']]=='file:///final/C379' and rels[byref['A6']]=='file:///old/shared'
    # No save/rebuild needed if a captured saved Test cell still requires Yes.
    test_change={'D5':dict(id='q1',id_cell='B5',old_value='No',new_value='Yes')}
    m.patch_existing_trade_numbers_and_links(source,tmp_path/'test-change.xlsx',updates,test_updates=test_change)
    assert m.audit_existing_trade_number_package(original,(tmp_path/'test-change.xlsx').read_bytes(),updates,test_updates=test_change)
    with pytest.raises(ValueError,match='identity'):
        m.patch_existing_trade_numbers_and_links(source,tmp_path/'refused.xlsx',{'A4':{**updates['A4'],'id':'other'}})


def test_crypto_number_repair_coordinated_transaction_rollback_and_collisions(tmp_path, monkeypatch):
    import sqlite3,ast,copy
    from tools import master_journal_workbook as m
    def prepared(name):
        base=tmp_path/name;base.mkdir();month=base/'CRYPTO/2026/OCTOBER';month.mkdir(parents=True)
        owners={'C379':'btc-owner','C380':'q1-owner','C381':'q2-owner','C382':'tree-owner','C383':'wet-owner','C384':'dood-owner','C386':'live-owner'}
        for number,owner in owners.items():
            folder=month/number;folder.mkdir();(folder/'.ltt-trade-owner.json').write_text(json.dumps(owner))
            if number!='C379':(folder/'chart.png').write_bytes((owner+' unique chart').encode())
        (month/'C381/notes').mkdir();(month/'C381/notes/manual.txt').write_text('unique manual note')
        wb=Workbook();ws=wb.active;ws.title='Trade Log'
        for col,header in enumerate(['Trade Number','Row ID','Formula','Test'],1):ws.cell(1,col,header)
        pairs=[('btc','BTCUSDT','C379'),('q1','QUSDT','C380'),('q2','QUSDT','C381'),('tree','TREEUSDT','C382'),('wet','WETUSDT','C383'),('dood','DOODUSDT','C384')]
        rows=[];updates={};numbers={'btc':None,'q1':'C379','q2':'C380','tree':'C381','wet':'C382','dood':'C383'}
        for i,(rid,symbol,number) in enumerate(pairs,4):
            row=dict(id=rid,symbol=symbol,account='Bybit Demo',trade_number=number,financial=100+i,is_test_trade=False,metrics={'keep':42});rows.append(row)
            ws.cell(i,1,number);ws.cell(i,2,rid);ws.cell(i,3,'=1+2');ws.cell(i,4,'Yes' if rid=='btc' else 'No');ws.cell(i,1).hyperlink=(month/number).as_uri()
            new=numbers[rid];updates['A'+str(i)]=dict(id=rid,id_cell='B'+str(i),old_number=number,new_number=new,old_link=(month/number).as_uri(),new_link=(month/new).as_uri() if new else None)
        workbook=base/'journal.xlsx';candidate=base/'candidate.xlsx';wb.save(workbook);wb.close();m.patch_existing_trade_numbers_and_links(workbook,candidate,updates)
        journal=base/'trading_journal.json';journal.write_text(json.dumps(dict(items=rows,unrelated={'value':42})))
        allocation=base/'render/data/trade_folder_allocations.json';allocation.parent.mkdir(parents=True)
        state={rid:dict(scope=['bybit','demo','','linear',symbol],identities=['order:'+rid+'-order'],row_id='accepted:'+rid,trade_number=num,folder=str(month/num),root=str(base/'CRYPTO'),owner=owners[num],status='ready') for rid,symbol,num in pairs}
        state['live']=dict(scope=['bybit','demo','','linear','BTWUSDT'],identities=['order:live-order'],row_id='accepted:live',trade_number='C386',folder=str(month/'C386'),root=str(base/'CRYPTO'),owner='live-owner',status='ready')
        allocation.write_text(json.dumps(state))
        ctx=[dict(broker='bybit',account='demo',category='linear',instrument='BTCUSDT',order_id='btc-order',side='Buy',is_test_trade=False,classification_version=0,early_trade_folder_request={'order_id':'btc-order','is_test_trade':False}),dict(order_id='live-order',instrument='BTWUSDT')]
        contexts=base/'contexts.json';contexts.write_text(json.dumps(ctx));stores=[]
        def changes(path,edits):
            doc=json.loads(path.read_text());out=[]
            for pointer,after in edits:
                target,key=repairer._number_pointer(doc,pointer);out.append(dict(pointer=pointer,before_exists=key in target,before=target.get(key),after=after))
            stores.append(dict(path=str(path),sha256=repairer._sha256(path),changes=out))
        edits=[(['items',{'id':r['id']},field],value) for r in rows for field,value in [('trade_number',numbers[r['id']]),('trade_folder',str(month/numbers[r['id']]) if numbers[r['id']] else None)]]
        manual={'is_test_trade':True,'manual_overrides':{'is_test_trade':True},'manual_override_fields':['is_test_trade'],'metrics':{'keep':42,'is_test_trade':True}}
        edits.extend((['items',{'id':'btc'},key],value) for key,value in manual.items());changes(journal,edits)
        allocation_edits=[]
        for rid,num in [*numbers.items(),('live','C384')]:
            allocation_edits.extend([([rid,'trade_number'],num),([rid,'folder'],str(month/num) if num else None)])
        allocation_edits.extend([(['btc','status'],'retired_test'),(['btc','retired_from'],{'trade_number':'C379','owner':'btc-owner'})]);changes(allocation,allocation_edits)
        changes(contexts,[([0,'classification_overrides'],{'is_test_trade':True}),([0,'classification_version'],1),([0,'early_trade_folder_request','retired_number_reservation'],{'reason':'explicit manual Test'}),([1,'trade_number'],'C384'),([1,'trade_folder'],str(month/'C384'))])
        db=base/'store.sqlite';sql=[]
        with sqlite3.connect(db) as con:
            for table in ('journal_trades','journal_metrics'):con.execute('CREATE TABLE '+table+'(id TEXT PRIMARY KEY,payload_json TEXT,imported_at TEXT)')
            for row in rows:
                raw=json.dumps(row);con.execute('INSERT INTO journal_trades VALUES(?,?,?)',(row['id'],raw,'unchanged'))
                fields=dict(trade_number=numbers[row['id']],trade_folder=str(month/numbers[row['id']]) if numbers[row['id']] else None)
                if row['id']=='btc':fields.update(manual)
                sql.append(dict(table='journal_trades',id=row['id'],before=raw,imported_at='unchanged',fields=fields))
            raw=json.dumps({'keep':42});con.execute('INSERT INTO journal_metrics VALUES(?,?,?)',('btc',raw,'unchanged'));sql.append(dict(table='journal_metrics',id='btc',before=raw,imported_at='unchanged',fields={'is_test_trade':True}))
        moves=[dict(source=str(month/a),destination=str(month/b),inventory=repairer._number_inventory(month/a)) for a,b in [('C380','C379'),('C381','C380'),('C382','C381'),('C383','C382'),('C384','C383'),('C386','C384')]]
        plan=dict(conflicts=[],test_classification=dict(id='btc',value=True,order_id='btc-order',test_cell='D4'),workbook_path=str(workbook),workbook_sha256=repairer._sha256(workbook),candidate_path=str(candidate),candidate_sha256=repairer._sha256(candidate),package_updates=updates,recovery_root=str(base/'recovery'),chart_root=str(base/'CRYPTO'),json_stores=stores,sqlite_path=str(db),sql_records=sql,folder_moves=moves,obsolete_scaffolds=[dict(path=str(month/'C379'),inventory=repairer._number_inventory(month/'C379'))],folder_creates=[],absent_after=[str(month/'C386')])
        baseline=dict(book=workbook.read_bytes(),stores={store['path']:json.loads(Path(store['path']).read_text()) for store in stores},folders={str(month/n):repairer._number_inventory(month/n) for n in owners})
        return base,plan,baseline
    def run(base,plan,failpoint=None):
        monkeypatch.setattr(m,'REPO_ROOT',base)
        return repairer.apply_number_repair_plan(plan,gate=lambda:repairer._number_repair_gate(Path(plan['workbook_path']),lock_path=base/'journal.lock'),failpoint=failpoint)
    base,plan,baseline=prepared('collision');(base/'CRYPTO/2026/OCTOBER/C379/unique-new-note').write_text('preserve')
    with pytest.raises(repairer.RepairRefused,match='scaffold'):run(base,plan)
    assert not (base/'recovery/apply.claim').exists() and Path(plan['workbook_path']).read_bytes()==baseline['book']
    for phase in ('folder_staged','sql_committed'):
        base,plan,baseline=prepared('rollback-'+phase)
        def fail(event):
            if event==phase:raise RuntimeError('injected '+phase)
        with pytest.raises(repairer.RepairRefused,match='"rollback_complete": true'):run(base,plan,fail)
        assert Path(plan['workbook_path']).read_bytes()==baseline['book']
        for path,value in baseline['stores'].items():assert json.loads(Path(path).read_text())==value
        for path,value in baseline['folders'].items():assert repairer._number_inventory(Path(path))==value
        with sqlite3.connect(plan['sqlite_path']) as con:
            for record in plan['sql_records']:assert con.execute('SELECT payload_json,imported_at FROM '+record['table']+' WHERE id=?',(record['id'],)).fetchone()==(record['before'],'unchanged')
    base,plan,baseline=prepared('success');result=run(base,plan);assert result['ok'] and result['folders_moved']==6 and result['scaffolds_removed']==1 and result['folders_created']==0
    assert Path(plan['workbook_path']).read_bytes()==Path(plan['candidate_path']).read_bytes()
    for move in plan['folder_moves']:assert repairer._number_inventory(Path(move['destination']))==move['inventory']
    assert not (base/'CRYPTO/2026/OCTOBER/C386').exists() and json.loads((base/'CRYPTO/2026/OCTOBER/C379/.ltt-trade-owner.json').read_text())=='q1-owner'
    state=json.loads((base/'render/data/trade_folder_allocations.json').read_text());assert state['btc']['status']=='retired_test' and state['btc']['trade_number'] is None and state['live']['trade_number']=='C384'
    journal=json.loads((base/'trading_journal.json').read_text());persisted=next(r for r in journal['items'] if r['id']=='btc');assert persisted['is_test_trade'] is True and persisted['manual_overrides']['is_test_trade'] is True and persisted['financial']==104
    # Exercise actual service classification/manual-precedence definitions without
    # service import/workers; only the fixture context I/O and clock are supplied.
    service=(Path(__file__).resolve().parents[1]/'render/master_service.py').read_text();tree=ast.parse(service)
    wanted={'_classification_for_journal','_apply_journal_classification','_apply_trading_journal_manual_overrides','_reapply_trading_journal_manual_overrides','_context_scope_compatible','_context_value_known','_normalize_side_for_comparison','_editable_trading_journal_fields'}
    definitions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in wanted];assert len(definitions)==len(wanted)
    contexts=json.loads((base/'contexts.json').read_text());ns={'_load_trade_contexts':lambda:contexts,'_utc_now_iso':lambda:'2026-11-01T00:00:00+00:00'}
    exec(compile(ast.Module(body=definitions,type_ignores=[]),'canonical service classification','exec'),ns)
    raw=dict(id='btc',broker='bybit',account='Bybit Demo',symbol='BTCUSDT',order_id='btc-order',side='Buy',is_test_trade=False,financial=104,manual_overrides=persisted['manual_overrides'])
    restored=ns['_apply_journal_classification'](raw);assert restored['is_test_trade'] is True and restored['financial']==104 and raw['is_test_trade'] is False
    overrides=m.read_master_journal_manual_overrides(Path(plan['workbook_path']));normalized,_=m._normalize_master_journal_rows([raw],overrides);assert normalized[0]['is_test_trade'] is True
    replay=dict(id='accepted:btc',broker='bybit',account='demo',symbol='BTCUSDT',order_id='btc-order',open_time='2026-10-18T09:21:51',is_test_trade=False)
    m.assign_trade_numbers_and_create_folders([replay],crypto_root=base/'CRYPTO',forex_root=base/'FOREX');assert 'trade_number' not in replay
    live=dict(id='genuine-later-import',broker='bybit',account='Bybit Demo',symbol='BTWUSDT',raw_refs={'order_ids':['live-order']},open_time='2026-10-31T22:51:43')
    m.assign_trade_numbers_and_create_folders([live],crypto_root=base/'CRYPTO',forex_root=base/'FOREX');assert live['trade_number']=='C384' and len(json.loads((base/'render/data/trade_folder_allocations.json').read_text()))==7
    assert run(base,plan)['noop'] is True
    with sqlite3.connect(plan['sqlite_path']) as con:
        assert con.execute('SELECT COUNT(*) FROM journal_trades').fetchone()[0]==6
        btc=json.loads(con.execute('SELECT payload_json FROM journal_trades WHERE id="btc"').fetchone()[0]);assert btc['is_test_trade'] is True and btc['financial']==104
    bad=copy.deepcopy(plan);bad['recovery_root']=str(base/'forbidden');bad['sql_records'][0]['fields']['net_profit']=0
    with pytest.raises(repairer.RepairRefused,match='Non-Test'):run(base,bad)
