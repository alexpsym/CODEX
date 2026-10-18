"""Two bounded Excel-style print-copy regressions; native adapter is faked here."""
import hashlib
import importlib.util
import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment

spec=importlib.util.spec_from_file_location('a4_journal_report',Path(__file__).parents[1]/'tools/build_trading_journal_report.py')
report=importlib.util.module_from_spec(spec);sys.modules[spec.name]=report;spec.loader.exec_module(report)


class Range:
    def __init__(self, sheet):
        self.sheet=sheet;self.Rows=self
    def Merge(self):pass
    def UnMerge(self):pass
    def Copy(self,Destination):self.sheet.excel.events.append('copy-group-context')
    def AutoFit(self):self.sheet.excel.events.append('local-row-expansion')


class Sheet:
    def __init__(self,name,book):
        self.Name=name;self.book=book;self.excel=book.excel;self.PageSetup=SimpleNamespace()
    def Range(self,*args):return Range(self)
    def Cells(self,*args):return Range(self)
    def Copy(self,After):
        After.book.Worksheets.items.append(Sheet(self.Name,After.book))
    def Delete(self):self.book.Worksheets.items.remove(self)


class Sheets:
    def __init__(self,book,names):self.items=[Sheet(name,book) for name in names]
    @property
    def Count(self):return len(self.items)
    def __call__(self,key):
        return self.items[key-1] if isinstance(key,int) else next(s for s in self.items if s.Name==key)


class Book:
    def __init__(self,excel,names=('Blank',)):
        self.excel=excel;self.Worksheets=Sheets(self,names)
    def Close(self,SaveChanges):
        assert SaveChanges is False
        self.excel.events.append('close-discard');self.excel.Workbooks.items.remove(self)
    def ExportAsFixedFormat(self,kind,path,OpenAfterPublish):
        assert kind==0 and OpenAfterPublish is False
        self.excel.events.append('native-pdf-export')
        self.excel.export_sheets=list(self.Worksheets.items)


class Books:
    def __init__(self,excel):self.excel=excel;self.items=[]
    @property
    def Count(self):return len(self.items)
    def Add(self,template):
        assert template==-4167
        b=Book(self.excel);self.items.append(b);self.excel.events.append('blank-add');return b
    def Open(self,path,**kwargs):
        assert self.Count>0
        assert Path(path).name=='print-copy.xlsx'
        assert kwargs==dict(UpdateLinks=0,ReadOnly=True,IgnoreReadOnlyRecommended=True,AddToMru=False)
        self.excel.events.append('owned-open')
        if self.excel.fail_open:raise RuntimeError('controlled native open failure')
        b=Book(self.excel,('Grid',));self.items.append(b);return b


class Excel:
    def __init__(self,fail_open=False):
        self.events=[];self.Workbooks=Books(self);self.fail_open=fail_open
        self._printer='physical inherited context'
    @property
    def ActiveSheet(self):return self.Workbooks.items[0].Worksheets(1) if self.Workbooks.Count else None
    @property
    def ActivePrinter(self):
        assert self.Workbooks.Count>0
        return self._printer
    @ActivePrinter.setter
    def ActivePrinter(self,value):
        assert self.Workbooks.Count>0
        assert value=='Microsoft Print to PDF on Ne01:'
        self.events.append('virtual-after-blank');self._printer=value
    def __setattr__(self,key,value):
        if key in ('Calculation','CalculateBeforeSave'):
            assert self.Workbooks.Count>0
            self.events.append(key)
        object.__setattr__(self,key,value)
    def Quit(self):
        assert self.Workbooks.Count==0
        self.events.append('owned-quit')


def test_excel_style_print_plan_preserves_grid_and_continuation_context(tmp_path):
    wb=Workbook();s=wb.active;s.title='Grid'
    s.column_dimensions['A'].width=12
    for col in 'BCDEFGHIJ':s.column_dimensions[col].width=32
    s.merge_cells('D1:F1');s['D1']='Management'
    for r in range(2,11):
        s.row_dimensions[r].height=21
        for c in range(1,11):
            cell=s.cell(r,c,f'R{r}C{c}')
            cell.font=Font(name='Cambria',size=11,bold=r==2)
            cell.fill=PatternFill('solid',fgColor='4472C4' if r==2 else 'FFFFFF')
            cell.border=Border(bottom=Side(style='thin',color='202020'))
            cell.alignment=Alignment(horizontal='left')
    panels=report.plan_panels(wb,[('Grid',10,2,1,'landscape',[(3,6),(7,10)])])
    assert [(p.first_column,p.last_column) for p in panels]==[(2,3),(4,7),(8,10)]*2
    assert [(p.first_row,p.last_row) for p in panels]==[(3,6)]*3+[(7,10)]*3
    expected={(r,c) for r in range(3,11) for c in range(2,11)}
    actual={(r,c) for p in panels for r in range(p.first_row,p.last_row+1) for c in range(p.first_column,p.last_column+1)}
    assert actual==expected
    assert s['B2'].font.name=='Cambria' and s['B2'].font.sz==11 and s['B2'].font.bold
    assert s['B3'].border.bottom.style=='thin' and s['B2'].fill.fgColor.rgb=='004472C4'
    assert s.column_dimensions['B'].width==32 and s.row_dimensions[3].height==21
    excel=Excel();book=excel.Workbooks.Add(-4167)
    for i,p in enumerate(panels,1):
        report.apply_native_page_setup(excel,book.Worksheets(1),p,i)
        settings=book.Worksheets(1).PageSetup
        assert settings.PaperSize==9 and settings.Orientation==2 and settings.Zoom==92
        assert settings.PrintTitleRows=='$1:$2' and settings.PrintTitleColumns=='$A:$A'
        assert settings.LeftMargin==28.35 and settings.TopMargin==36
        assert settings.Order==2 and settings.BlackAndWhite is False and settings.PrintHeadings
        assert f'rows {p.first_row}-{p.last_row}' in settings.CenterFooter
        assert f'panel {p.panel}/3' in settings.CenterFooter
        assert 11*settings.Zoom/100>=10
    assert str(s.merged_cells)=='D1:F1'
    book.Close(SaveChanges=False);excel.Quit()


def test_print_copy_preserves_source_values_styles_and_grayscale_structure(tmp_path):
    wb=Workbook();s=wb.active;s.title='Grid';s.merge_cells('A1:C1');s['A1']='Group heading'
    s['A3']=0;s['B3']=False;s['C3']=-12.345;s['D3']=0.0000000123
    s['C3'].number_format='0.000" AUD"';s['D3'].number_format='0.0000000000'
    s['A4']='=1-1';s['B4']='=2+2';s['C4']='=3+3';s['D4']='Full long note  '*100+'END'
    s['A1'].font=Font(name='Cambria',size=11,bold=True,color='FFFFFF')
    s['A1'].fill=PatternFill('solid',fgColor='112288');s['B3'].fill=PatternFill('solid',fgColor='F0D050')
    s['C3'].border=Border(bottom=Side(style='double',color='006600'))
    s.column_dimensions['D'].width=40;s.row_dimensions[4].height=34
    original=tmp_path/'source.xlsx';wb.save(original)
    source=original.read_bytes();buffer=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as z,zipfile.ZipFile(buffer,'w') as out:
        for item in z.infolist():
            data=z.read(item.filename)
            if item.filename=='xl/worksheets/sheet1.xml':
                root=ET.fromstring(data)
                for cell in root.findall('.//s:c',report.NS):
                    if cell.get('r') in ('A4','B4'):cell.find('s:v',report.NS).text='0' if cell.get('r')=='A4' else '4'
                data=ET.tostring(root)
            out.writestr(item,data)
    original.write_bytes(buffer.getvalue());before=original.read_bytes();mtime=original.stat().st_mtime_ns
    capture=report.capture_workbook(original,tmp_path)
    copied=report.prepare_print_copy(capture,tmp_path)
    assert copied!=original and copied.parent==tmp_path
    values=load_workbook(copied,data_only=True);v=values['Grid']
    assert [v.cell(3,c).value for c in range(1,5)]==[0,False,-12.345,.0000000123]
    assert v['A4'].value==0 and v['B4'].value==4 and v['C4'].value=='[stored result unavailable]'
    assert v['D4'].value=='Full long note  '*100+'END'
    assert v['A1'].font.name=='Cambria' and v['A1'].font.sz==11 and v['A1'].font.bold
    assert v['A1'].font.color.rgb=='FFFFFFFF'
    assert v['C3'].border.bottom.style=='double'
    assert v['C3'].number_format=='0.000" AUD"' and v['D3'].number_format=='0.0000000000'
    assert str(v.merged_cells)=='A1:C1' and v.column_dimensions['D'].width==40 and v.row_dimensions[4].height==34
    colors=[v['A1'].fill.fgColor.rgb,v['B3'].fill.fgColor.rgb]
    assert colors[0]!=colors[1]
    for color in colors:assert color[-6:-4]==color[-4:-2]==color[-2:]
    values.close()
    with zipfile.ZipFile(copied) as z:
        assert b'<f' not in z.read('xl/worksheets/sheet1.xml')
    panels=[report.PrintPanel('Grid',2,4,3,4,2,1,'landscape',1,1,1)]
    excel=Excel();report.native_preview(copied,tmp_path,panels,'Microsoft Print to PDF on Ne01:',factory=lambda:excel)
    assert excel.events.index('blank-add')<excel.events.index('Calculation')<excel.events.index('virtual-after-blank')<excel.events.index('owned-open')
    assert excel.events.count('native-pdf-export')==1 and excel.events.count('close-discard')==3
    assert excel.events[-1]=='owned-quit'
    failed=Excel(True)
    try:report.native_preview(copied,tmp_path,panels,'Microsoft Print to PDF on Ne01:',factory=lambda:failed)
    except RuntimeError as error:assert str(error)=='controlled native open failure'
    else:raise AssertionError('Expected controlled failure')
    assert failed.events[-2:]==['close-discard','owned-quit']
    for path,context in [(original,'Microsoft Print to PDF on Ne01:'),(copied,'Brother on Ne03:'),(copied,'network-printer')]:
        try:report.native_preview(path,tmp_path,panels,context,factory=lambda:(_ for _ in ()).throw(AssertionError('Must reject before native creation')))
        except ValueError:pass
        else:raise AssertionError('Unsafe path/context accepted')
    assert original.read_bytes()==before and original.stat().st_mtime_ns==mtime
    assert capture.sha256==hashlib.sha256(before).hexdigest()
