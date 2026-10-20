"""Synthetic-only regression coverage for the bounded Forex recovery pilot."""
import io
import base64
import json
import os
from pathlib import Path
import zipfile
from xml.sax.saxutils import escape

from PIL import Image
import pytest

from tools import recover_forex_word_documents as recovery


NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
      'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"')
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def paragraph(text, properties="", run_properties=""):
    chunks = text.split("\n")
    content = "<w:br/>".join('<w:t xml:space="preserve">'+escape(x)+"</w:t>" for x in chunks)
    return "<w:p><w:pPr>"+properties+"</w:pPr><w:r><w:rPr>"+run_properties+"</w:rPr>"+content+"</w:r></w:p>"


def cell(text, cell_properties="", **kwargs):
    return "<w:tc><w:tcPr>"+cell_properties+"</w:tcPr>"+paragraph(text, **kwargs)+"</w:tc>"


def table():
    return ('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>'
            '<w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2200"/></w:tblGrid>'
            '<w:tr>'+cell("Price Ω — 0.0000000123", '<w:gridSpan w:val="2"/><w:shd w:fill="E0EBFF"/>',
                            properties='<w:jc w:val="center"/>', run_properties='<w:b/><w:color w:val="123456"/>')+'</w:tr>'
            '<w:tr>'+cell("No", '<w:vMerge w:val="restart"/><w:vAlign w:val="center"/>')+cell("0")+'</w:tr>'
            '<w:tr>'+cell("", '<w:vMerge/>')+cell("1.00000001 AUD\n± 2%", run_properties='<w:i/><w:u w:val="single"/>')+'</w:tr></w:tbl>')


def package(*, decorated=False, external=False, unsafe=False, unsupported=False, nonempty_metadata=False):
    png = io.BytesIO()
    Image.new("RGB", (3, 2), (12, 100, 200)).save(png, "PNG")
    picture = png.getvalue()
    body = table()+table()
    if decorated:
        box = ('<w:drawing><a:prstGeom prst="rect"/><wps:txbx><w:txbxContent>'
               +table()+paragraph("List item — exact", properties='<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
               +'</w:txbxContent></wps:txbx></w:drawing>')
        fallback = '<w:pict><w:txbxContent>'+table()+paragraph("List item — exact", properties='<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')+'</w:txbxContent></w:pict>'
        body = (paragraph("Before")+body+'<w:p><w:r><mc:AlternateContent>'
                '<mc:Choice Requires="wps">'+box+'</mc:Choice><mc:Fallback>'+fallback
                +'</mc:Fallback></mc:AlternateContent></w:r></w:p>'
                +paragraph("After\nPreserve 0 / No")
                +'<w:p><w:r><w:drawing><a:blip r:embed="image1"/></w:drawing></w:r></w:p>')
    if unsupported:
        body += '<w:p><w:r><w:drawing><a:prstGeom prst="triangle"/></w:drawing><w:fldChar w:fldCharType="begin"/></w:r></w:p>'
    entries = {"word/document.xml": '<w:document '+NS+'><w:body>'+body+'</w:body></w:document>'}
    if decorated:
        entries.update({
            "word/header1.xml": '<w:hdr '+NS+'>'+paragraph("Header — Ω")+'<w:p><w:r><w:drawing><a:blip r:embed="headerimage"/></w:drawing></w:r></w:p></w:hdr>',
            "word/media/image1.png": picture,
            "word/_rels/document.xml.rels": '<Relationships xmlns="'+REL+'"><Relationship Id="image1" Type="'+IMAGE_REL+'" Target="media/image1.png"/></Relationships>',
            "word/_rels/header1.xml.rels": '<Relationships xmlns="'+REL+'"><Relationship Id="headerimage" Type="'+IMAGE_REL+'" Target="media/image1.png"/></Relationships>',
            "word/numbering.xml": '<w:numbering '+NS+'><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="&#xF0B7;"/><w:rPr><w:rFonts w:ascii="Symbol"/></w:rPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>',
            "customXml/item1.xml": '<b:Sources xmlns:b="http://schemas.openxmlformats.org/officeDocument/2006/bibliography" SelectedStyle="APA.xsl" StyleName="APA" Version="6">'+('<b:Source/>' if nonempty_metadata else '')+'</b:Sources>',
            "customXml/itemProps1.xml": '<ds:datastoreItem xmlns:ds="http://schemas.openxmlformats.org/officeDocument/2006/customXml" ds:itemID="{11111111-2222-3333-4444-555555555555}"><ds:schemaRefs><ds:schemaRef ds:uri="http://schemas.openxmlformats.org/officeDocument/2006/bibliography"/></ds:schemaRefs></ds:datastoreItem>',
            "customXml/_rels/item1.xml.rels": '<Relationships xmlns="'+REL+'"><Relationship Id="props" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXmlProps" Target="itemProps1.xml"/></Relationships>',
        })
    if external:
        entries["word/_rels/external.xml.rels"] = '<Relationships xmlns="'+REL+'"><Relationship Id="external" Type="'+IMAGE_REL+'" Target="https://example.invalid/no-fetch.png" TargetMode="External"/></Relationships>'
    if unsafe:
        entries["../Crypto/escape.txt"] = "MUST NOT WRITE"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in entries.items(): z.writestr(name, content)
    return out.getvalue(), picture


def fixture_selection(base, *, external=False, nonempty_metadata=False):
    root = base/"FOREX"; root.mkdir(parents=True)
    crypto = base/"Crypto"; crypto.mkdir(); (crypto/"keep.txt").write_bytes(b"untouched")
    documents = []; originals = {}
    for i in range(5):
        # Identical long stems in distinct folders must never collide.
        p = root/"2018"/("F%d" % (i+1))/("same_"+"long"*20+".docx")
        p.parent.mkdir(parents=True)
        data, picture = package(decorated=i>0, external=external and i==0,
                                nonempty_metadata=nonempty_metadata and i==1)
        p.write_bytes(data); originals[p] = data
        d = dict(relative_path=p.relative_to(root).as_posix(), kind="document", extension=".docx",
                 availability="metadata_local_candidate", skipped_link=False,
                 size=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns)
        documents.append(d)
    manifest = base/"manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(x) for x in documents), encoding="utf-8")
    chosen = [dict(d, known_sha256=recovery.digest(originals[root/d["relative_path"]]),
                   known_tables=2 if i==0 else 4, known_media=0 if i==0 else 1)
              for i,d in enumerate(documents)]
    selection = base/"selection.json"
    recovery.save(selection, dict(root=str(root), inventory=str(manifest),
                                 inventory_sha256=recovery.digest(manifest.read_bytes()), documents=chosen))
    evidence = base/"evidence"; evidence.mkdir()
    return selection,evidence,originals,crypto,picture


def review(evidence):
    p = recovery.load(evidence/"preparation.json")
    path = evidence/"review.json"
    recovery.save(path, dict(approved=True, preparation_sha256=recovery.digest((evidence/"preparation.json").read_bytes()),
                            selection_sha256=p["selection_sha256"]))
    return path


def test_docx_recovery_preserves_images_tables_text_and_provenance(tmp_path, monkeypatch):
    selection,evidence,originals,crypto,picture = fixture_selection(tmp_path)
    prepared = recovery.prepare(selection,evidence)
    assert prepared["status"] == "complete" and prepared["source_count"] == 5
    assert prepared["deletion_eligible"] is False
    assert sum(len(x["coverage"]["tables"]) for x in prepared["documents"]) == 14
    assert sum(x["coverage"]["raw_table_count"] for x in prepared["documents"]) == 18
    all_names = []
    for index,result in enumerate(prepared["documents"]):
        coverage = result["coverage"]
        assert not coverage["issues"]
        t = coverage["tables"][0]
        assert [(c["row"],c["column"],c["rowspan"],c["colspan"]) for c in t["cells"]] == [(0,0,1,2),(1,0,2,1),(1,1,1,1),(2,1,1,1)]
        assert [c["text"] for c in t["cells"]] == ["Price Ω — 0.0000000123","No","0","1.00000001 AUD\n± 2%"]
        assert t["cells"][0]["paragraphs"][0]["runs"][0]["bold"]
        assert t["cells"][-1]["paragraphs"][0]["runs"][0]["italic"]
        assert t["cells"][0]["shading"] == "E0EBFF"
        for table_data in coverage["tables"]:
            rendered = table_data["rendering"]
            assert rendered["font_pixels"] >= 16 and rendered["pixels"] <= 20_000_000
            for cell_data,measure in zip(table_data["cells"],rendered["cells"]):
                assert measure["exact_text"] == cell_data["text"]
                assert "".join(x["character"] for x in measure["glyphs"]) == cell_data["text"].replace("\n", "")
                x0,y0,x1,y1 = measure["rectangle"]
                assert all(x0 <= g["box"][0] <= g["box"][2] <= x1 and y0 <= g["box"][1] <= g["box"][3] <= y1 for g in measure["glyphs"])
        for item in result["outputs"]:
            private = Path(item["private"]); all_names.append(item["name"])
            assert not Path(item["destination"]).exists()  # prepare is private
            if item["kind"] == "picture":
                assert private.read_bytes() == picture
                assert item["sha256"] == recovery.digest(picture)
                assert len(coverage["media"][0]["relationships"]) == 2
                assert len(coverage["media"][0]["occurrences"]) == 2
            elif item["kind"] == "table":
                with Image.open(private) as image:
                    image.load(); assert image.format == "PNG" and image.width*image.height <= 20_000_000
                    assert image.getpixel((20,20)) == (224,235,255)
            else:
                text = private.read_text(encoding="utf-8")
                assert text.index("Before") < text.index("List item — exact") < text.index("After")
                assert "•\tList item — exact" in text and "Header — Ω" in text
                assert "word/header1.xml" in text and "textbox_1" in text
        if index:
            assert sum(x["alternate_alias"] for x in coverage["table_coverage"]) == 1
            assert len(coverage["metadata_parts"]) == 3
            with zipfile.ZipFile(result["capture"]) as original:
                for m in coverage["metadata_parts"]:
                    assert m["classification"] == "empty_bibliography_metadata"
                    assert base64.b64decode(m["original_bytes_base64"]) == original.read(m["part"])
                    assert m["sha256"] == recovery.digest(original.read(m["part"]))
    assert len(all_names) == len(set(all_names))
    recovery.validate_prepared(selection,evidence)
    replay = tmp_path/"saved_copy_replay"; replay.mkdir()
    def no_source(*args, **kwargs):
        raise AssertionError("Saved-copy preparation must not access original source paths")
    with monkeypatch.context() as patch:
        patch.setattr(recovery, "locked_source", no_source)
        patch.setattr(recovery, "safe_path", no_source)
        repeated = recovery.prepare(selection, replay, evidence)
    assert repeated["status"] == "complete" and repeated["source_captures"] == 0
    assert repeated["saved_capture_origin"]["preparation_sha256"] == recovery.digest((evidence/"preparation.json").read_bytes())
    assert [{o["name"]:o["sha256"] for o in r["outputs"]} for r in repeated["documents"]] == [{o["name"]:o["sha256"] for o in r["outputs"]} for r in prepared["documents"]]
    recovery.validate_prepared(selection, replay)
    published = recovery.publish(selection,evidence,review(evidence))
    assert published["status"] == "complete" and len(published["source_checks"]) == 5
    assert len(published["published"]) == 22  # 14 table PNGs + 4 pictures + 4 text companions
    for result in prepared["documents"]:
        for item in result["outputs"]:
            assert Path(item["destination"]).read_bytes() == Path(item["private"]).read_bytes()
    assert all(p.read_bytes() == data for p,data in originals.items())
    assert (crypto/"keep.txt").read_bytes() == b"untouched"


def test_forex_pilot_rejects_source_changes_collisions_and_unsafe_paths(tmp_path):
    def case(name, **kwargs): return fixture_selection(tmp_path/name, **kwargs)
    for mode in ("changed_capture","missing","collision","changed_hash","nonforex","unsafe_source","larger","linked","nonempty_metadata","drawing"):
        selection,evidence,originals,crypto,_ = case(mode, external=mode=="linked", nonempty_metadata=mode=="nonempty_metadata")
        source = next(iter(originals))
        if mode in ("nonforex","unsafe_source","larger"):
            data = recovery.load(selection)
            if mode == "nonforex": data["root"] = str(crypto)
            elif mode == "unsafe_source": data["documents"][0]["relative_path"] = "../Crypto/keep.docx"
            else: data["documents"].append(data["documents"][0])
            recovery.save(selection,data)
            with pytest.raises(recovery.RecoveryError): recovery.prepare(selection,evidence)
        elif mode in ("changed_capture","missing"):
            if mode == "changed_capture": source.write_bytes(b"changed")
            else: source.unlink()  # synthetic fixture only
            with pytest.raises((recovery.RecoveryError,FileNotFoundError)): recovery.prepare(selection,evidence)
        elif mode == "linked":
            assert recovery.prepare(selection,evidence)["status"] == "incomplete"
            with pytest.raises(recovery.RecoveryError): recovery.publish(selection,evidence,review(evidence))
            assert not any(recovery.load(evidence/"preparation.json")["documents"][i]["complete"] is False for i in range(1,5))
        elif mode == "nonempty_metadata":
            incomplete = recovery.prepare(selection,evidence)
            assert incomplete["status"] == "incomplete"
            assert any("Custom XML needs" in issue for issue in incomplete["documents"][1]["coverage"]["issues"])
            with pytest.raises(recovery.RecoveryError): recovery.publish(selection,evidence,review(evidence))
        elif mode == "drawing":
            data,_ = package(unsupported=True)
            parsed = recovery.parse_package(data)
            assert "Unsupported drawing" in parsed["issues"]
            assert "Unsupported meaningful feature: fldChar" in parsed["issues"]
        else:
            p = recovery.prepare(selection,evidence)
            item = p["documents"][0]["outputs"][0]
            target = Path(item["destination"])
            if mode == "collision": target.write_bytes(b"existing user output")
            else:
                before = source.stat()
                changed = bytearray(source.read_bytes()); changed[-1] ^= 1
                source.write_bytes(changed); os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns))
            with pytest.raises(recovery.RecoveryError): recovery.publish(selection,evidence,review(evidence))
            if mode == "collision": assert target.read_bytes() == b"existing user output"
            ledger = recovery.load(evidence/"publication.json")
            assert ledger["status"] == "incomplete" and not ledger["published"]
        assert (crypto/"keep.txt").read_bytes() == b"untouched"
        assert not list(crypto.glob("*.png"))
        for path,data in originals.items():
            if path != source or mode not in ("changed_capture","changed_hash","missing"):
                assert path.read_bytes() == data
    for name in ("../Crypto/bad.png","/absolute.png","C:/outside.png","word/../../bad.xml"):
        with pytest.raises(recovery.RecoveryError): recovery.safe_relative(name)
    with pytest.raises(recovery.RecoveryError): recovery.parse_package(package(unsafe=True)[0])
    data,_ = package(decorated=True)
    for mode in ("unknown_schema", "binding", "unexpected_attribute"):
        altered = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(altered,"w") as target:
            for item in source.infolist():
                raw = source.read(item.filename)
                if mode == "unknown_schema" and item.filename == "customXml/itemProps1.xml":
                    raw = raw.replace(b'uri="http://schemas.openxmlformats.org/officeDocument/2006/bibliography"',b'uri="urn:unknown"')
                if mode == "binding" and item.filename == "word/document.xml":
                    raw = raw.replace(b'<w:body>',b'<w:body><w:dataBinding/>')
                if mode == "unexpected_attribute" and item.filename == "customXml/item1.xml":
                    raw = raw.replace(b'Version="6"',b'Version="6" Unrecognized="yes"')
                target.writestr(item,raw)
        assert any("Custom XML" in issue for issue in recovery.parse_package(altered.getvalue())["issues"])


def test_forex_batch_bounds_and_completed_source_exclusion(tmp_path, monkeypatch):
    from collections import Counter
    from copy import deepcopy

    pilot_selection,pilot_evidence,originals,crypto,_ = fixture_selection(tmp_path/"archive")
    pilot = recovery.load(pilot_selection)
    root = Path(pilot["root"])
    inventory = [json.loads(x) for x in Path(pilot["inventory"]).read_text(encoding="utf-8").splitlines()]
    new_documents = []
    for index in range(22):
        source = root/"2018"/("F%03d" % (100+index))/"trade.docx"
        source.parent.mkdir(parents=True)
        xml = ('<w:document '+NS+'><w:body><w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>'
               '<w:tblGrid><w:gridCol w:w="3000"/></w:tblGrid><w:tr>'
               +cell("%d / 0.00001 / No" % index)+'</w:tr></w:tbl></w:body></w:document>')
        data = io.BytesIO()
        with zipfile.ZipFile(data,"w",zipfile.ZIP_DEFLATED) as z: z.writestr("word/document.xml",xml)
        source.write_bytes(data.getvalue()); originals[source] = data.getvalue()
        d = dict(relative_path=source.relative_to(root).as_posix(),kind="document",extension=".docx",
                 availability="metadata_local_candidate",skipped_link=False,
                 size=source.stat().st_size,mtime_ns=source.stat().st_mtime_ns)
        inventory.append(d); new_documents.append(d)
    manifest = Path(pilot["inventory"])
    manifest.write_text("\n".join(json.dumps(x) for x in inventory),encoding="utf-8")
    pilot["inventory_sha256"] = recovery.digest(manifest.read_bytes()); recovery.save(pilot_selection,pilot)
    prepared_pilot = recovery.prepare(pilot_selection,pilot_evidence)
    pilot_receipt = recovery.publish(pilot_selection,pilot_evidence,review(pilot_evidence))
    for item in pilot_receipt["published"]:
        assert recovery.digest(Path(item["path"]).read_bytes()) == item["sha256"]
    recovery.save(pilot_evidence/"published_verification.json",dict(status="complete",documents=5,
        published_files=len(pilot_receipt["published"]),counts=dict(Counter(o["kind"] for o in pilot_receipt["published"]))))
    recovery.save(pilot_evidence/"preflight.json",dict(tool_sha256=prepared_pilot["tool_sha256"]))
    record_names=("selection.validated.json","preparation.json","publication.json","published_verification.json","preflight.json")
    reference=dict(directory=str(pilot_evidence),tool_sha256=prepared_pilot["tool_sha256"],
                   records={n:recovery.digest((pilot_evidence/n).read_bytes()) for n in record_names})
    batch=dict(root=str(root),inventory=str(manifest),inventory_sha256=pilot["inventory_sha256"],
               mode="batch",batch_limit=20,selected_count=20,documents=new_documents[:20],prior_completions=[reference])
    selection=tmp_path/"batch.json"; recovery.save(selection,batch)
    evidence=tmp_path/"batch_evidence"; evidence.mkdir()
    with pytest.raises(recovery.RecoveryError): recovery.selection_data(selection)  # pilot default stays strict
    prepared = recovery.prepare(selection,evidence,mode="batch")
    assert prepared["status"]=="complete" and prepared["source_count"]==20 and prepared["selected_count"]==20
    assert prepared["mode"]=="batch" and prepared["prior_completions"]==[reference]
    recovery.validate_prepared(selection,evidence,mode="batch")
    receipt = recovery.publish(selection,evidence,review(evidence),mode="batch")
    assert receipt["status"]=="complete" and len(receipt["source_checks"])==20
    assert len(receipt["published"])==20
    assert {x["path"] for x in receipt["published"]}=={o["destination"] for d in prepared["documents"] for o in d["outputs"]}
    assert all(p.read_bytes()==data for p,data in originals.items())

    for d in new_documents[20:]:
        folder=(root/d["relative_path"]).parent
        assert list(folder.iterdir())==[root/d["relative_path"]]
    assert (crypto/"keep.txt").read_bytes()==b"untouched"

    def no_source(*args, **kwargs):
        raise AssertionError("Invalid selection/saved-copy validation touched an original")
    for mode in ("empty","over20","completed","duplicate","case_variant","count","mode","outside","receipt_hash"):
        bad=deepcopy(batch)
        if mode=="empty": bad["documents"]=[]; bad["selected_count"]=0
        elif mode=="over20": bad["documents"]=new_documents[:21]; bad["selected_count"]=21
        elif mode=="completed": bad["documents"][0]=pilot["documents"][0]
        elif mode in ("duplicate","case_variant"):
            bad["documents"][1]=deepcopy(bad["documents"][0])
            if mode=="case_variant":bad["documents"][1]["relative_path"]=bad["documents"][1]["relative_path"].upper()
        elif mode=="count": bad["selected_count"]=19
        elif mode=="mode": bad["mode"]="pilot"
        elif mode=="outside": bad["documents"][0]["relative_path"]="../Crypto/trade.docx"
        else:bad["prior_completions"][0]["records"]["publication.json"]="0"*64
        path=tmp_path/(mode+".json"); recovery.save(path,bad)
        out=tmp_path/(mode+"_evidence");out.mkdir()
        with monkeypatch.context() as patch:
            patch.setattr(recovery,"locked_source",no_source)
            with pytest.raises(recovery.RecoveryError):recovery.prepare(path,out,mode="batch")
    for count in (0,4,6):
        bad=deepcopy(pilot);bad["documents"]=(pilot["documents"]*2)[:count]
        path=tmp_path/("pilot%d.json"%count);recovery.save(path,bad)
        with pytest.raises(recovery.RecoveryError):recovery.selection_data(path)

    altered=tmp_path/"altered_receipt";altered.mkdir()
    for n in record_names:(altered/n).write_bytes((pilot_evidence/n).read_bytes())
    (altered/"publication.json").write_text('{"status":"incomplete"}',encoding="utf-8")
    bad=deepcopy(batch);bad["prior_completions"][0]["directory"]=str(altered)
    path=tmp_path/"changed_receipt.json";recovery.save(path,bad)
    with pytest.raises(recovery.RecoveryError):recovery.selection_data(path,mode="batch")

    original_preparation=(evidence/"preparation.json").read_bytes()
    broken=deepcopy(prepared);broken["source_count"]=19;recovery.save(evidence/"preparation.json",broken)
    with pytest.raises(recovery.RecoveryError):recovery.validate_prepared(selection,evidence,mode="batch")
    (evidence/"preparation.json").write_bytes(original_preparation)
    changed=deepcopy(batch);changed["documents"]=changed["documents"][:19];changed["selected_count"]=19
    path=tmp_path/"changed_list.json";recovery.save(path,changed)
    with pytest.raises(recovery.RecoveryError):recovery.validate_prepared(path,evidence,mode="batch")

    small=deepcopy(batch);small["documents"]=new_documents[20:];small["selected_count"]=2
    small_selection=tmp_path/"small.json";recovery.save(small_selection,small)
    small_evidence=tmp_path/"small_evidence";small_evidence.mkdir()
    # Actual supervised child must receive --mode batch; default pilot would reject 2.
    assert recovery.main(["--stage","prepare","--mode","batch","--selection",str(small_selection),
                          "--output",str(small_evidence),"--supervise"])==0
    _,small_prepared=recovery.validate_prepared(small_selection,small_evidence,mode="batch")
    assert small_prepared["source_count"]==2
    replay=tmp_path/"small_replay";replay.mkdir()
    with monkeypatch.context() as patch:
        patch.setattr(recovery,"locked_source",no_source);patch.setattr(recovery,"safe_path",no_source)
        assert recovery.prepare(small_selection,replay,small_evidence,mode="batch")["source_captures"]==0
    broken=deepcopy(small_prepared);broken["source_count"]=1;recovery.save(small_evidence/"preparation.json",broken)
    bad_replay=tmp_path/"bad_replay";bad_replay.mkdir()
    with pytest.raises(recovery.RecoveryError):recovery.prepare(small_selection,bad_replay,small_evidence,mode="batch")
    assert all(p.read_bytes()==data for p,data in originals.items())


def _segmentation_package(variant=None, tables_in_branch=True):
    """Synthetic Choice/Fallback with genuine run fragmentation, not trade data."""
    fmt='<w:rPr><w:b/><w:color w:val="123456"/></w:rPr>'
    head='<w:p><w:pPr><w:jc w:val="left"/></w:pPr>'
    choice_p=head+'<w:r>'+fmt+'<w:t xml:space="preserve">Keep Ω </w:t><w:br/><w:t>same</w:t><w:tab/><w:t>0.00001</w:t></w:r></w:p>'
    fallback_p=head+'<w:r>'+fmt+'<w:t xml:space="preserve">Keep </w:t></w:r><w:r>'+fmt+'<w:t xml:space="preserve">Ω </w:t><w:br/><w:t>sa</w:t><w:t>me</w:t><w:tab/><w:t>0.00001</w:t></w:r></w:p>'
    image='<w:p><w:r><w:drawing><a:blip r:embed="image1"/></w:drawing></w:r></w:p>' if tables_in_branch else ''
    left=(table() if tables_in_branch else '')+choice_p+paragraph("Next paragraph")+image
    right=(table() if tables_in_branch else '')+fallback_p+paragraph("Next paragraph")+image
    if variant=="text":right=right.replace('<w:t>sa</w:t>','<w:t>changed</w:t>')
    elif variant=="order":right=paragraph("Next paragraph")+right.replace(paragraph("Next paragraph"),'')
    elif variant=="paragraph":right=right.replace('<w:t>sa</w:t>','</w:r></w:p>'+head+'<w:r>'+fmt+'<w:t>sa</w:t>')
    elif variant=="break":right=right.replace('<w:br/>','')
    elif variant=="tab":right=right.replace('<w:tab/>','<w:t> </w:t>')
    elif variant=="merge":right=right.replace('<w:gridSpan w:val="2"/>','<w:gridSpan w:val="1"/>',1)
    elif variant=="cell":right=right.replace(cell("0"),cell("0")+cell(""),1)
    elif variant=="image":right=right.replace('r:embed="image1"','r:embed="different"')
    elif variant=="format":right=right.replace('<w:color w:val="123456"/>','<w:color w:val="654321"/>',1)
    elif variant=="unknown":right=right.replace('<w:t>sa</w:t>','<w:fldSimple w:instr="UNKNOWN"/>')
    alternate=('<mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><a:prstGeom prst="rect"/><wps:txbx><w:txbxContent>'
               +left+'</w:txbxContent></wps:txbx></w:drawing></mc:Choice><mc:Fallback><w:pict><w:txbxContent>'
               +right+'</w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent>')
    body=table()+(table() if not tables_in_branch else '')+'<w:p><w:r>'+alternate+'</w:r></w:p>'
    result=io.BytesIO()
    with zipfile.ZipFile(result,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml",'<w:document '+NS+'><w:body>'+body+'</w:body></w:document>')
        if tables_in_branch:
            picture=package()[1];z.writestr("word/media/image1.png",picture)
            z.writestr("word/_rels/document.xml.rels",'<Relationships xmlns="'+REL+'"><Relationship Id="image1" Type="'+IMAGE_REL+'" Target="media/image1.png"/></Relationships>')
    return result.getvalue()


def test_alternate_content_equivalence_preserves_structure_and_rejects_conflicts():
    import xml.etree.ElementTree as ET
    parsed=recovery.parse_package(_segmentation_package())
    assert not parsed["issues"] and parsed["raw_table_count"]==3 and len(parsed["tables"])==2
    assert sum(x["alternate_alias"] for x in parsed["table_coverage"])==1
    proof=parsed["alternate_content"][0]
    assert proof["equivalent"] and proof["branches"][0]["signature"]!=proof["branches"][1]["signature"]
    assert proof["branches"][0]["normalized_signature"]==proof["branches"][1]["normalized_signature"]
    assert [x["text"] for x in parsed["prose"] if "text" in x]==["Keep Ω \nsame\t0.00001","Next paragraph"]
    assert len(parsed["image_occurrences"])==1
    for variant in ("text","order","paragraph","break","tab","merge","cell","image","format","unknown"):
        with zipfile.ZipFile(io.BytesIO(_segmentation_package(variant))) as z:
            root=ET.fromstring(z.read("word/document.xml"))
        alt=next(root.iter(recovery.MC+"AlternateContent"))
        assert not recovery.alternate_proof(alt)["equivalent"],variant
        issues=[];recovery.collapse_alternatives(root,issues)
        assert "Divergent alternate-content branches" in issues,variant


def test_saved_capture_correction_allows_only_proven_incomplete_branch_changes(tmp_path, monkeypatch):
    from copy import deepcopy
    selection,old,originals,_,_=fixture_selection(tmp_path/"archive")
    chosen=recovery.load(selection);source=next(iter(originals));data=_segmentation_package(tables_in_branch=False)
    source.write_bytes(data);originals[source]=data
    records=[json.loads(x) for x in Path(chosen["inventory"]).read_text(encoding="utf-8").splitlines()]
    records[0].update(size=len(data),mtime_ns=source.stat().st_mtime_ns)
    chosen["documents"][0].update(records[0],known_sha256=recovery.digest(data))
    Path(chosen["inventory"]).write_text("\n".join(json.dumps(x) for x in records),encoding="utf-8")
    chosen["inventory_sha256"]=recovery.digest(Path(chosen["inventory"]).read_bytes());recovery.save(selection,chosen)
    # Reproduce legacy token comparison only to generate real old failed outputs;
    # canonical capture/parser/renderer produce the entire snapshot, never a mock result.
    with monkeypatch.context() as patch:
        patch.setattr(recovery,"normalized_branch_signature",recovery.branch_signature)
        prior=recovery.prepare(selection,old)
    assert prior["status"]=="incomplete" and not prior["documents"][0]["complete"]
    assert all(r["complete"] for r in prior["documents"][1:])
    relative=chosen["documents"][0]["relative_path"]
    authorization=recovery.branch_correction_record(selection,old,relative)
    correction=tmp_path/"correction.json";recovery.save(correction,authorization)
    assert authorization["allowed_changed_kinds"]==["text"]
    def no_source(*args,**kwargs):raise AssertionError("Saved correction accessed archive originals")
    def attempt(name,path=correction):
        out=tmp_path/name;out.mkdir()
        with monkeypatch.context() as patch:
            patch.setattr(recovery,"locked_source",no_source);patch.setattr(recovery,"safe_path",no_source)
            return recovery.prepare(selection,out,old,correction=path)
    for field in ("old_preparation_sha256","selection_sha256","source_sha256"):
        invalid=deepcopy(authorization);invalid[field]="0"*64;path=tmp_path/(field+".json");recovery.save(path,invalid)
        with pytest.raises(recovery.RecoveryError):attempt("bad_"+field,path)
    with pytest.raises(recovery.RecoveryError):recovery.branch_correction_record(selection,old,chosen["documents"][1]["relative_path"])
    capture=Path(prior["documents"][0]["capture"]);unchanged=capture.read_bytes();capture.write_bytes(unchanged+b"changed")
    with pytest.raises(recovery.RecoveryError):attempt("changed_capture")
    capture.write_bytes(unchanged)
    old_bytes=(old/"preparation.json").read_bytes()
    wrong=deepcopy(prior);wrong["source_count"]=4;recovery.save(old/"preparation.json",wrong)
    with pytest.raises(recovery.RecoveryError):attempt("wrong_completion")
    (old/"preparation.json").write_bytes(old_bytes)
    selected_bytes=selection.read_bytes();wrong_selection=deepcopy(chosen);wrong_selection["unexpected_change"]=True;recovery.save(selection,wrong_selection)
    with pytest.raises(recovery.RecoveryError):attempt("wrong_selection")
    selection.write_bytes(selected_bytes)
    wrong=deepcopy(prior);wrong["documents"][1]["outputs"][0]["sha256"]="0"*64;recovery.save(old/"preparation.json",wrong)
    altered_authorization=recovery.branch_correction_record(selection,old,relative)
    altered=tmp_path/"altered_correction.json";recovery.save(altered,altered_authorization)
    with pytest.raises(recovery.RecoveryError):attempt("unrelated_output",altered)
    (old/"preparation.json").write_bytes(old_bytes)
    current=attempt("corrected")
    assert current["status"]=="complete" and current["source_captures"]==0
    assert current["documents"][0]["saved_copy_correction"]["changed_outputs"]
    for index,(new,previous) in enumerate(zip(current["documents"],prior["documents"])):
        a={o["name"]:(o["kind"],o["sha256"]) for o in new["outputs"]}
        b={o["name"]:(o["kind"],o["sha256"]) for o in previous["outputs"]}
        assert set(a)==set(b)
        if index:assert a==b
        else:assert all(a[n][0]==b[n][0]=="text" for n in a if a[n]!=b[n])
    assert all(path.read_bytes()==raw for path,raw in originals.items())
