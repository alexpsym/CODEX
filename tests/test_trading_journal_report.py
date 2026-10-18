"""Two bounded, production-independent print regressions."""
import hashlib
import importlib.util
import io
from pathlib import Path
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import PatternFill

spec = importlib.util.spec_from_file_location('a4_journal_report', Path(__file__).parents[1]/'tools/build_trading_journal_report.py')
report = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = report
spec.loader.exec_module(report)


def test_a4_trade_log_bands_cover_fields_and_preserve_readable_page_bounds(tmp_path):
    # Independent full current schema; omission from renderer constants must fail.
    expected = ['Trade Number','Open Time','Close Time','Account','Symbol','Side','Qty',
        'Entry Price','Exit Price','Stop Loss Price','Stop Loss Distance','Target Price','Target Distance',
        'Commission','Net P/L','Profit %','R-Multiple','Balance After','Trade Duration (DD:HH:MM:SS)',
        'Move to Break Even Time','Move to Break Even Duration','Move to Break Even Trigger Price',
        'Move to Break Even Distance From Entry %','Move to Break Even Distance From Exit %',
        'Move to Profit Time','Move to Profit Duration','Move to Profit Trigger Price',
        'Move to Profit Distance From Entry %','Move to Profit Distance From Exit %','Test','Pattern','EMA',
        'VWAP','ATHS/ATLS','Order','Round Number','Spiked Out','Close Stopout','Near Perfect Entry','Near Win',
        'Early Close','Setup','Timeframe','Breakeven','Notes','Cashflow Amount','Cashflow New Balance','Currency','Row Type','Row ID']
    assert list(report.TRADE_FIELDS) == expected
    rows = report.synthetic_rows()
    assert len(rows) == 2
    assert rows[0]['Entry Price'] == '0.0000000123'
    assert rows[0]['Net P/L'] == '-12.345 AUD'
    assert rows[1]['Net P/L'] == '0 AUD'
    assert rows[0]['Test'] == 'No'
    assert rows[0]['Profit %'] == '-0.125%'
    assert rows[0]['R-Multiple'] == '-0.375R'
    # Independently replace the long text with a known, whitespace-sensitive value.
    note = ('Observation 00: keep  two spaces, zero 0 and negative -0.125%. ' * 24)+'FINAL-SENTINEL'
    rows[0]['Notes'] = note
    expected_values = {r['Source row']: dict(r) for r in rows}
    pages = report.build_layout_preview(rows)
    try:
        assert 9 <= len(pages) <= 10
        assert any(p.band == 6 and 'continued' in p.title for p in pages)
        assert pages[0].figure.get_size_inches()[0] < pages[0].figure.get_size_inches()[1]
        assert pages[1].figure.get_size_inches()[0] > pages[1].figure.get_size_inches()[1]
        actual = {r['Source row']: {} for r in rows}
        for p in pages:
            w,h = p.figure.get_size_inches()*72
            assert sorted((w,h)) == sorted(report.A4)
            for item in p.placements:
                assert item.artist in p.figure.texts
                assert item.font_size >= (9 if item.role == 'footer' else 10)
                x0,y0,x1,y1 = item.bounds
                assert 36-0.1 <= x0 < x1 <= w-36+0.1
                assert (10 if item.role == 'footer' else 36)-0.1 <= y0 < y1 <= h-36+0.1
                if p.band and item.role == 'data':
                    if p.band < 6:
                        actual[item.row][item.column] = actual[item.row].get(item.column,'') + item.artist.get_text().replace('\n','')
                    elif item.column != 'Field':
                        row,col = item.column.split('|',1)
                        actual[row][col] = actual[row].get(col,'') + item.artist.get_text().replace('\n','')
            if p.band:
                headers = ''.join(i.artist.get_text().replace('\n','') for i in p.placements if i.role == 'header')
                if p.band < 6:
                    for key in ('Source row','Trade Number','Account','Symbol'):
                        assert key in headers
                    for row in rows:
                        placed = [i.row for i in p.placements if i.role == 'data' and i.column == 'Source row']
                        assert placed == [r['Source row'] for r in rows]
                else:
                    for row in rows:
                        for key in ('Source row','Trade Number','Account','Symbol'):
                            assert row[key] in headers
            body = [i for i in p.placements if i.role in ('data','header','group','continuation')]
            for j,a in enumerate(body):
                for b in body[j+1:]:
                    ax,ay,ar,at=a.bounds; bx,by,br,bt=b.bounds
                    assert min(ar,br)-max(ax,bx) <= .1 or min(at,bt)-max(ay,by) <= .1
        for row, values in expected_values.items():
            assert set(actual[row]) == set(expected) | {'Source row'}
            for key in expected:
                # Repeated identity fields occur once on each horizontal band.
                factor = 5 if key in ('Trade Number','Account','Symbol') else 1
                assert actual[row][key] == values[key]*factor, (row,key)
        assert actual['Trade Log!4']['Notes'] == note
        target=tmp_path/'fixture.pdf'
        assert report.write_preview_pdf(pages,target) == len(pages)
        data=target.read_bytes()
        assert data.startswith(b'%PDF-')
        boxes=re.findall(rb'/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]',data)
        assert len(boxes) == len(pages)
        for w,h in boxes:
            assert all(abs(a-b)<1e-5 for a,b in zip(sorted((float(w),float(h))),sorted(report.A4)))
    finally:
        report.close_pages(pages)


def test_print_snapshot_preserves_cached_values_and_never_writes_source(tmp_path,monkeypatch):
    workbook=Workbook();ws=workbook.active;ws.title='Business'
    ws.merge_cells('A1:C1');ws['A1']='Merged business label'
    ws['A2']=0;ws['B2']=False;ws['C2']=-12.345;ws['C2'].number_format='0.000" AUD"'
    ws['A3']='=1-1';ws['B3']='=1+1';ws['C3']='=2+2'
    ws['A4']=0.0000000123;ws['B4']=Decimal('-.00125');ws['B4'].number_format='0.000%'
    ws['C4']=Decimal('-.375');ws['C4'].number_format='0.000"R"'
    ws['XFD1000000'].fill=PatternFill('solid',fgColor='FFFFFF')
    workbook.create_sheet('_Target R Metadata').sheet_state='veryHidden'
    workbook.create_sheet('Hidden business').sheet_state='hidden'
    workbook['Hidden business']['A1']='Business information must remain included'
    source=tmp_path/'source.xlsx';workbook.save(source)
    original=source.read_bytes()
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as archive,zipfile.ZipFile(out,'w') as dest:
        for info in archive.infolist():
            data=archive.read(info.filename)
            if info.filename=='xl/worksheets/sheet1.xml':
                root=ET.fromstring(data)
                for node in root.findall('.//s:c',report.NS):
                    if node.get('r') in ('A3','B3'):
                        node.find('s:v',report.NS).text='0' if node.get('r')=='A3' else '2'
                data=ET.tostring(root)
            dest.writestr(info,data)
    source.write_bytes(out.getvalue());before=source.read_bytes();stat=source.stat()
    def forbidden_save(*a,**k):
        raise AssertionError('Print reader must never save a workbook')
    monkeypatch.setattr(Workbook,'save',forbidden_save)
    captured=report.capture_workbook(source,tmp_path)
    sheets=report.read_print_snapshot(captured)
    assert source.read_bytes()==before
    assert source.stat().st_mtime_ns==stat.st_mtime_ns
    assert captured.sha256==hashlib.sha256(before).hexdigest()
    assert Path(captured.temporary_path).read_bytes()==before
    assert [s.name for s in sheets]==['Business','_Target R Metadata','Hidden business']
    assert sheets[0].bounds=='A1:C4'
    assert sheets[0].merges==['A1:C1']
    assert sheets[1].exclusion and sheets[1].visibility=='veryHidden'
    assert sheets[2].exclusion is None and sheets[2].visibility=='hidden'
    cells={c.coordinate:c for c in sheets[0].cells}
    assert report.display_value(cells['A2'])=='0'
    assert report.display_value(cells['B2'])=='No'
    assert report.display_value(cells['C2'])=='-12.345 AUD'
    assert report.display_value(cells['A3'])=='0'
    assert report.display_value(cells['B3'])=='2'
    assert cells['C3'].missing_cache
    assert report.display_value(cells['C3'])=='[stored result unavailable]'
    assert sum(c.missing_cache for c in cells.values())==1
    assert report.display_value(cells['A4'])=='0.0000000123'
    assert report.display_value(cells['B4'])=='-0.12500%'
    assert report.display_value(cells['C4'])=='-0.375 R'
    assert '0.000"R"' in sheets[0].format_limitations
