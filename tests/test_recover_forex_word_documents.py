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
