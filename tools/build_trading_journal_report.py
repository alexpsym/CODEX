#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "render" / "data" / "trading_journal_view_cache.json"
REPORT_PATH = ROOT / "render" / "data" / "trading_journal_report.html"


def legacy_main() -> int:
    if not SNAPSHOT_PATH.exists():
        print(f"Snapshot not found: {SNAPSHOT_PATH}")
        return 1
    payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    items = payload.get("items") or []
    stats = payload.get("stats") or {}
    diagnostics = payload.get("diagnostics") or {}
    html = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Trading Journal Report</title>
<style>body{{font-family:Arial,sans-serif;margin:20px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:6px;font-size:12px}}th{{background:#f5f5f5}}</style>
</head><body>
<h1>Trading Journal Report</h1>
<p>Generated snapshot: {payload.get('generated_at','')}</p>
<p>Total rows: {len(items)}</p>
<h2>Overview</h2>
<pre>{json.dumps((stats or {}).get('groups', {}).get('overview', {}), indent=2)}</pre>
<h2>Diagnostics</h2>
<pre>{json.dumps(diagnostics, indent=2)}</pre>
<h2>Rows</h2>
<table>
<tr><th>Close Time</th><th>Account</th><th>Symbol</th><th>Side</th><th>Net Profit</th><th>Result %</th><th>Balance After</th></tr>
{''.join(f"<tr><td>{r.get('close_time') or r.get('open_time') or ''}</td><td>{r.get('account_label') or r.get('account') or ''}</td><td>{r.get('symbol') or ''}</td><td>{r.get('side') or ''}</td><td>{r.get('net_profit') or ''}</td><td>{r.get('result_pct') or ''}</td><td>{r.get('analysis_balance_after_trade') or r.get('balance_after_trade') or r.get('cashflow_new_balance') or ''}</td></tr>" for r in items)}
</table>
</body></html>"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")
    return 0


# Print primitives intentionally do not import the workbook generator or service.
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
import hashlib
import io
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
TECHNICAL_SHEETS = {'_Target R Metadata': 'Internal target-R provenance; not a business report'}


@dataclass(frozen=True)
class Capture:
    source: str
    sha256: str
    size: int
    mtime_ns: int
    data: bytes = field(repr=False)
    temporary_path: str = ''


def capture_workbook(source: Path, temporary_directory: Path) -> Capture:
    """One attempt, one byte read; never opens the source for writing or retries."""
    before = source.stat()
    data = source.read_bytes()
    after = source.stat()
    digest = hashlib.sha256(data).hexdigest()
    target = temporary_directory / 'captured-workbook.xlsx'
    with target.open('xb') as stream:
        stream.write(data)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != before.st_size:
        raise RuntimeError(f'Source changed during the sole capture; preserved bytes: {target}; SHA256={digest}')
    return Capture(str(source), digest, len(data), before.st_mtime_ns, data, str(target))


@dataclass(frozen=True)
class PrintCell:
    coordinate: str
    value: object
    number_format: str
    formula: str | None = None
    missing_cache: bool = False


@dataclass
class SheetSnapshot:
    name: str
    visibility: str
    cells: list[PrintCell]
    bounds: str
    merges: list[str]
    tables: list[dict]
    objects: dict
    exclusion: str | None
    format_limitations: list[str]


def read_print_snapshot(capture: Capture) -> list[SheetSnapshot]:
    """Parse inert OOXML only; populated cells determine bounds, not dimensions.

    Resource guards fail honestly rather than scanning unbounded formatting padding.
    Formula expressions are retained as provenance, never substituted for results.
    """
    from openpyxl.styles.stylesheet import Stylesheet
    from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format
    from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter
    from openpyxl.utils.datetime import from_excel, CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900
    import posixpath
    with zipfile.ZipFile(io.BytesIO(capture.data)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 200_000_000:
            raise ValueError('Snapshot exceeds 200 MB expanded scan budget')
        def xml(path):
            return ET.fromstring(archive.read(path))
        def relations(path):
            folder, base = posixpath.split(path)
            relpath = f'{folder}/_rels/{base}.rels'
            if relpath not in archive.namelist():
                return {}
            return {r.attrib['Id']: (r.attrib['Type'].split('/')[-1],
                    posixpath.normpath(posixpath.join(folder, r.attrib['Target'])).lstrip('/')
                    if not r.attrib['Target'].startswith('/') else r.attrib['Target'].lstrip('/'))
                    for r in xml(relpath) if r.attrib.get('TargetMode') != 'External'}
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = [''.join(n.itertext()) for n in xml('xl/sharedStrings.xml')]
        styles = Stylesheet.from_tree(xml('xl/styles.xml'))
        formats = dict(BUILTIN_FORMATS)
        formats.update({n.numFmtId: n.formatCode for n in styles.numFmts.numFmt})
        workbook = xml('xl/workbook.xml')
        props = workbook.find('s:workbookPr', NS)
        epoch = CALENDAR_MAC_1904 if props is not None and props.get('date1904') in ('1', 'true') else CALENDAR_WINDOWS_1900
        links = relations('xl/workbook.xml')
        result = []
        scanned = 0
        for sheet in workbook.find('s:sheets', NS):
            path = links[sheet.attrib[REL]][1]
            root = xml(path)
            cells, limitations = [], set()
            for node in root.findall('.//s:sheetData/s:row/s:c', NS):
                scanned += 1
                if scanned > 1_000_000:
                    raise ValueError('Snapshot exceeds one million serialized cells')
                formula = node.find('s:f', NS)
                value = node.find('s:v', NS)
                inline = node.find('s:is', NS)
                if formula is None and value is None and inline is None:
                    continue
                text = value.text if value is not None else None
                kind = node.get('t', 'n')
                missing = formula is not None and text is None
                if inline is not None:
                    raw = ''.join(t.text or '' for t in inline.findall('.//s:t', NS))
                elif kind == 's' and text is not None:
                    raw = shared[int(text)]
                elif kind == 'b' and text is not None:
                    raw = text == '1'
                elif kind in ('str', 'e', 'd'):
                    raw = text
                else:
                    raw = Decimal(text) if text is not None else None
                fmt = formats.get(styles.cellXfs.xf[int(node.get('s', '0'))].numFmtId, 'General')
                if isinstance(raw, Decimal) and is_date_format(fmt):
                    raw = from_excel(float(raw), epoch=epoch)
                elif fmt != 'General':
                    limitations.add(fmt)
                cells.append(PrintCell(node.attrib['r'], raw, fmt,
                                       formula.text or '' if formula is not None else None, missing))
            coordinates = [coordinate_to_tuple(c.coordinate) for c in cells]
            bounds = ''
            if coordinates:
                rows, cols = zip(*coordinates)
                bounds = f'{get_column_letter(min(cols))}{min(rows)}:{get_column_letter(max(cols))}{max(rows)}'
            rels = relations(path)
            tables = []
            objects = {'charts': 0, 'images': 0, 'comments': 0, 'other_relationships': []}
            for kind, target in rels.values():
                if kind == 'table':
                    table = xml(target)
                    tables.append({'name': table.get('displayName'), 'ref': table.get('ref')})
                elif kind == 'drawing':
                    for drawing_kind, _ in relations(target).values():
                        if drawing_kind in ('chart', 'image'):
                            objects['charts' if drawing_kind == 'chart' else 'images'] += 1
                        else:
                            objects['other_relationships'].append(drawing_kind)
                elif kind == 'comments':
                    objects['comments'] += len(xml(target).findall('.//s:comment', NS))
                else:
                    objects['other_relationships'].append(kind)
            result.append(SheetSnapshot(sheet.get('name'), sheet.get('state', 'visible'), cells, bounds,
                [m.get('ref') for m in root.findall('s:mergeCells/s:mergeCell', NS)], tables, objects,
                TECHNICAL_SHEETS.get(sheet.get('name')), sorted(limitations)))
        return result


def display_value(cell: PrintCell) -> str:
    """Lossless fallback; complex Excel display formats require Phase 6B work."""
    if cell.missing_cache:
        return '[stored result unavailable]'
    value = cell.value
    if value is None:
        return '[missing]'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=' ') if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (Decimal, int, float)):
        number = Decimal(str(value))
        if '%' in cell.number_format:
            return f'{number * 100:f}%'
        text = f'{number:f}'
        currency = next((c for c in '$£€' if c in cell.number_format), '')
        suffixes = re.findall(r'"([^"#0]*)"', cell.number_format)
        unit = ''.join(suffixes).strip()
        if not unit and re.search(r'\\R', cell.number_format):
            unit = 'R'
        return currency + text + ((' ' + unit) if unit and unit not in currency else '')
    return str(value)


# Current 50-field schema, including the merged management subheaders. This is
# schema only, copied from the canonical producer and checked against the capture.
TRADE_FIELDS = (
    'Trade Number', 'Open Time', 'Close Time', 'Account', 'Symbol', 'Side', 'Qty',
    'Entry Price', 'Exit Price', 'Stop Loss Price', 'Stop Loss Distance',
    'Target Price', 'Target Distance', 'Commission', 'Net P/L', 'Profit %',
    'R-Multiple', 'Balance After', 'Trade Duration (DD:HH:MM:SS)',
    *[f'Move to {group} {suffix}' for group in ('Break Even', 'Profit')
      for suffix in ('Time', 'Duration', 'Trigger Price', 'Distance From Entry %', 'Distance From Exit %')],
    'Test', 'Pattern', 'EMA', 'VWAP', 'ATHS/ATLS', 'Order', 'Round Number',
    'Spiked Out', 'Close Stopout', 'Near Perfect Entry', 'Near Win', 'Early Close',
    'Setup', 'Timeframe', 'Breakeven', 'Notes', 'Cashflow Amount',
    'Cashflow New Balance', 'Currency', 'Row Type', 'Row ID',
)
REPEATED_KEYS = ('Source row', 'Trade Number', 'Account', 'Symbol')
BANDS = (
    ('Identity / execution', ('Open Time', 'Close Time', 'Side', 'Qty', 'Trade Duration (DD:HH:MM:SS)')),
    ('Prices / risk', TRADE_FIELDS[7:13]),
    ('Results', TRADE_FIELDS[13:18]),
    ('Move to Break Even', TRADE_FIELDS[19:24]),
    ('Move to Profit', TRADE_FIELDS[24:29]),
    ('Classifications / administration / notes', tuple(f for f in TRADE_FIELDS[29:] if f != 'Notes') + ('Notes',)),
)
A4 = (210 / 25.4 * 72, 297 / 25.4 * 72)
MARGIN = 36.0
BODY_FONT = 10.0


def synthetic_rows():
    """No production values, identifiers or account labels enter the fixture."""
    rows = []
    for i in range(2):
        row = {f: '[missing]' for f in TRADE_FIELDS}
        row.update({'Source row': f'Trade Log!{4+i}', 'Trade Number': '[missing]' if i else 'SYN-001',
                    'Account': 'Demo A', 'Symbol': 'SYNUSD', 'Side': 'Buy' if i == 0 else 'Sell',
                    'Open Time': '2026-10-01 09:00+10:00', 'Close Time': '2026-10-01 10:00+10:00',
                    'Qty': '0', 'Entry Price': '0.0000000123', 'Exit Price': '0.0000000120',
                    'Stop Loss Price': '0.0000000110', 'Stop Loss Distance': '0.0000000013',
                    'Target Price': '0.0000000150', 'Target Distance': '0.0000000027',
                    'Commission': '-0.0004 USDT', 'Net P/L': '-12.345 AUD' if i == 0 else '0 AUD',
                    'Profit %': '-0.125%', 'R-Multiple': '-0.375R', 'Balance After': '1000.000 AUD',
                    'Trade Duration (DD:HH:MM:SS)': '00:01:00:00', 'Test': 'No', 'Pattern': 'Range',
                    'EMA': 'No', 'VWAP': 'Yes', 'ATHS/ATLS': 'No', 'Order': 'Limit',
                    'Round Number': 'No', 'Spiked Out': 'No', 'Close Stopout': 'Yes',
                    'Near Perfect Entry': 'No', 'Near Win': 'No', 'Early Close': 'No',
                    'Setup': 'Pullback', 'Timeframe': '30MIN', 'Breakeven': 'No',
                    'Cashflow Amount': '0 AUD', 'Cashflow New Balance': '[missing]', 'Currency': 'AUD',
                    'Row Type': 'trade', 'Row ID': f'synthetic-{i+1}',
                    'Notes': ('Preserve the full observation, including the zero result and the small price. '
                              'Review entry timing before the next planned session. ' * 10 + 'END OF LONG NOTE')
                              if i == 0 else 'No early exit. END OF SECOND NOTE'})
        for group in ('Break Even', 'Profit'):
            for suffix, value in zip(('Time', 'Duration', 'Trigger Price', 'Distance From Entry %', 'Distance From Exit %'),
                                     ('2026-10-01 09:30+10:00', '00:00:30:00', '0.0000000123', '0%', '-0.125%')):
                row[f'Move to {group} {suffix}'] = value
        rows.append(row)
    return rows


@dataclass
class PlacedText:
    artist: object
    row: str | None
    column: str | None
    original: str
    role: str
    bounds: tuple = ()
    font_size: float = BODY_FONT


@dataclass
class PrintPage:
    figure: object
    title: str
    band: int | None = None
    placements: list[PlacedText] = field(default_factory=list)


def build_layout_preview(rows=None):
    """Return owned A4 page objects with measured, source-addressed placements.

    Large administrative bands use a field/record matrix, retaining both record
    columns on every continuation. Long cells split at measured line boundaries.
    """
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.font_manager import FontProperties
    rows = synthetic_rows() if rows is None else rows
    if not rows:
        raise ValueError('Preview requires a small shared row block')
    pages = []

    def page(title, landscape=False, band=None):
        w, h = reversed(A4) if landscape else A4
        fig = Figure(figsize=(w/72, h/72), dpi=72)
        FigureCanvasAgg(fig)
        p = PrintPage(fig, title, band)
        pages.append(p)
        place(p, title, MARGIN, h-40, 16, role='title')
        place(p, 'Layout preview — synthetic data', MARGIN, h-61, 10, role='label')
        return p

    def place(p, text, x, y, size=BODY_FONT, row=None, column=None, original=None, role='data', bold=False):
        w, h = p.figure.get_size_inches()*72
        artist = p.figure.text(x/w, y/h, text, va='top', ha='left', fontsize=size,
                               fontfamily='DejaVu Sans', weight='bold' if bold else 'normal', color='#111111',
                               linespacing=1.3)
        p.placements.append(PlacedText(artist, row, column, text if original is None else original, role, font_size=size))

    def wrap(p, text, width, bold=False):
        renderer = p.figure.canvas.get_renderer()
        prop = FontProperties(family='DejaVu Sans', size=BODY_FONT, weight='bold' if bold else 'normal')
        def fits(s):
            return renderer.get_text_width_height_descent(s, prop, False)[0] <= width
        lines = []
        # Preserve every character, including whitespace, across line fragments.
        for paragraph in str(text).split('\n'):
            current = ''
            for token in re.findall(r'\S+\s*|\s+', paragraph):
                if fits(current+token):
                    current += token
                else:
                    if current:
                        lines.append(current)
                    current = token
                    if not fits(current):
                        # Do not split numbers: a wider band is required instead.
                        if re.fullmatch(r'[-+\d.,%]+\s*\w*', current.strip()):
                            raise ValueError(f'Numeric value needs a wider column: {current}')
                        chunk = ''
                        for ch in current:
                            if chunk and not fits(chunk+ch):
                                lines.append(chunk)
                                chunk = ''
                            chunk += ch
                        current = chunk
            lines.append(current)
        return lines

    def table(p, headers, records, widths, y, band=None, groups=None):
        """records: [(stable-row-id, [(column-id, display-string), ...])]."""
        title = p.title
        def start(target):
            h = target.figure.get_size_inches()[1]*72
            top = h-88
            if groups:
                for label, start_col, span in groups:
                    place(target, label, MARGIN+sum(widths[:start_col])+4, top, bold=True, role='group')
                top -= 21
            x = MARGIN
            height = max(len(wrap(target, label, width-8, True)) for label, width in zip(headers, widths))*13+12
            from matplotlib.patches import Rectangle
            target.figure.add_artist(Rectangle((MARGIN/(target.figure.get_size_inches()[0]*72), (top-height)/(h)),
                                     sum(widths)/(target.figure.get_size_inches()[0]*72), height/h,
                                     transform=target.figure.transFigure, color='#eeeeee', zorder=0))
            for label, width in zip(headers, widths):
                place(target, '\n'.join(wrap(target,label,width-8,True)), x+4, top-4, role='header', bold=True)
                x += width
            return top-height-7
        y = start(p)
        for row_id, values in records:
            fragments = [wrap(p, value, width-8) for (_, value), width in zip(values, widths)]
            while any(fragments):
                available = int((y-MARGIN-12)//13)
                if available < 1:
                    p = page(title+' / continued' if not title.endswith('/ continued') else title, True, band)
                    y = start(p)
                    if row_id:
                        place(p, f'Continued row: {row_id}', MARGIN, y, role='continuation')
                        y -= 22
                    available = int((y-MARGIN-12)//13)
                take = min(max(map(len, fragments)), available)
                x = MARGIN
                for index, ((column, original), width) in enumerate(zip(values,widths)):
                    chunk = fragments[index][:take]
                    if chunk:
                        place(p, '\n'.join(chunk), x+4, y, row=row_id, column=column, original=original)
                    fragments[index] = fragments[index][take:]
                    x += width
                y -= take*13+4
        return p

    p = page('Trading journal | Summary')
    width = A4[0]-2*MARGIN
    table(p, ['Metric','Synthetic value'], [(None, [('Metric',k),('Value',v)]) for k,v in
        [('Trades','2'),('Wins','0'),('Losses','1'),('Break-even','1'),('Net P/L','-12.345 AUD'),
         ('Net R Multiple','-0.375R'),('Test','No'),('Missing value','[missing]')]], [width*.58,width*.42],0)
    place(p, 'Read at Actual size / 100%. Workbook is unchanged.', MARGIN, 290, role='label')
    place(p, 'Trade Log records repeat across six consecutive field bands.', MARGIN, 266, role='label')
    p = page('SYMBOLS | Comparison / statistics', True)
    width = A4[1]-2*MARGIN
    table(p,['Symbol','Trades','Wins','Losses','Net R Multiple','Net P/L %'],
          [(None,[(str(j),v) for j,v in enumerate(r)]) for r in
           [('SYNUSD','2','0','1','-0.375R','-0.125%'),('SYNJPY','0','0','0','0R','0%')]],
          [width/6]*6,0, groups=[('Activity',0,4),('Results',4,2)])
    for number, (name, fields) in enumerate(BANDS,1):
        p = page(f'Trade Log | Band {number}/6 | {name}', True, number)
        width = A4[1]-2*MARGIN
        if number < 6:
            columns = REPEATED_KEYS+tuple(fields)
            widths = [86,72,58,65]+[(width-281)/len(fields)]*len(fields)
            table(p, columns, [(row['Source row'],[(col,row[col]) for col in columns]) for row in rows], widths,0,number)
        else:
            # Each record remains its own column; keys appear in every header.
            headers = ['Field']+[' | '.join(row[k] for k in REPEATED_KEYS) for row in rows]
            widths = [210]+[(width-210)/len(rows)]*len(rows)
            records = [(f,[('Field',f)]+[(row['Source row']+'|'+f,row[f]) for row in rows]) for f in fields]
            table(p,headers,records,widths,0,number)
    p = page('YEARLY REPORT | Month detail')
    width = A4[0]-2*MARGIN
    table(p,['Month','Trades','Net P/L','Net R Multiple'],
          [(None,[(str(j),v) for j,v in enumerate(r)]) for r in
           [('October','2','-12.345 AUD','-0.375R'),('November','0','0 AUD','0R')]],
          [width*.24,width*.15,width*.31,width*.30],0)
    if len(pages)>10:
        close_pages(pages)
        raise ValueError('Synthetic proof exceeded ten pages; reduce fixture rows, never font size')
    for i,p in enumerate(pages,1):
        place(p, f'{i} / {len(pages)}', MARGIN, 23, size=9, role='footer')
        p.figure.canvas.draw()
        renderer = p.figure.canvas.get_renderer()
        w,h = p.figure.get_size_inches()*72
        for item in p.placements:
            item.bounds = tuple(item.artist.get_window_extent(renderer).extents)
            x0,y0,x1,y1 = item.bounds
            minimum_y = 10 if item.role == 'footer' else MARGIN
            if x0 < MARGIN-0.1 or x1 > w-MARGIN+0.1 or y0 < minimum_y or y1 > h-MARGIN+0.1:
                close_pages(pages)
                raise ValueError(f'Text outside printable bounds on {p.title}: {item.artist.get_text()!r} {item.bounds}')
    return pages


def close_pages(pages):
    for page in pages:
        page.figure.clear()


def write_preview_pdf(pages, output: Path, inspection_directory: Path | None = None):
    import matplotlib as mpl
    from matplotlib.backends.backend_pdf import PdfPages
    output.parent.mkdir(parents=True, exist_ok=True)
    with mpl.rc_context({'pdf.fonttype':42, 'text.usetex':False}), PdfPages(output, metadata={
        'Title':'Trading journal - Layout preview - synthetic data', 'Author':'LTT'}) as pdf:
        for p in pages:
            pdf.savefig(p.figure)  # No tight bounding box: physical A4 dimensions stay fixed.
    if inspection_directory is not None:
        inspection_directory.mkdir(parents=True,exist_ok=True)
        indices = [0, 2, 3, next(i for i,p in reversed(list(enumerate(pages))) if p.band == 6)]
        for i in indices:
            pages[i].figure.savefig(inspection_directory/f'page-{i+1}.png',dpi=100)
    return len(pages)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description='Legacy HTML report or synthetic A4 layout proof')
    parser.add_argument('--print-proof', type=Path, help='Write a synthetic PDF; never reads production data')
    parser.add_argument('--inspection-directory', type=Path, help='At most four PNGs from the same page objects')
    args = parser.parse_args()
    if args.print_proof is None:
        if args.inspection_directory:
            parser.error('--inspection-directory requires --print-proof')
        return legacy_main()
    pages = build_layout_preview()
    try:
        print(f'Wrote {write_preview_pdf(pages,args.print_proof,args.inspection_directory)} A4 pages: {args.print_proof}')
    finally:
        close_pages(pages)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
