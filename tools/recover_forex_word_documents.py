"""Manifest-driven five-document Forex pilot; default preparation is private.

No discovery, source deletion, journal updates, Office or relationship fetching.
Detailed content, provenance, captures and ledgers stay in explicit HOST storage.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import posixpath
import re
import subprocess
import sys
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont, __version__ as PILLOW_VERSION

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
MAX_SOURCE = 20 * 1024**2
MAX_TOTAL = 80 * 1024**2
MAX_PIXELS = 20_000_000
FONT_SIZE = 18


class RecoveryError(ValueError):
    """Unsafe input, unsupported content or invalid publication evidence."""


def require(condition, message):
    if not condition:
        raise RecoveryError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


def safe_relative(name):
    require(isinstance(name, str) and name and "\\" not in name,
            "Expected safe POSIX relative path")
    p = PurePosixPath(name)
    require(not p.is_absolute() and not PureWindowsPath(name).drive
            and all(x not in ("", ".", "..") for x in name.split("/"))
            and ":" not in name, "Unsafe relative path")
    require(not any(c in name for c in '<>"|?*') and all(
        not x.endswith((" ", ".")) and x.split(".")[0].upper() not in
        {"CON", "PRN", "AUX", "NUL", *["COM%d" % i for i in range(1,10)],
         *["LPT%d" % i for i in range(1,10)]} for x in p.parts), "Unsafe Windows path component")
    return p


def metadata(path):
    s = Path(path).stat(follow_symlinks=False)
    return dict(size=s.st_size, mtime_ns=s.st_mtime_ns,
                attributes=getattr(s, "st_file_attributes", 0),
                reparse_tag=getattr(s, "st_reparse_tag", 0))


def cloud(tag):
    return bool(tag and tag & 0xFFFF0FFF == 0x9000001A)


def safe_path(root, relative):
    rel = safe_relative(relative)
    root = Path(root)
    require(root.is_absolute() and root.name.upper() == "FOREX", "Non-FOREX root")
    path = root.joinpath(*rel.parts)
    require(path.resolve().is_relative_to(root.resolve()), "Path escapes FOREX")
    for p in [root, *[root.joinpath(*rel.parts[:i]) for i in range(1, len(rel.parts))], path]:
        require(not p.is_symlink(), "Linked source/output ancestor")
        if p.exists():
            m = metadata(p)
            require(not m["attributes"] & 0x400 or cloud(m["reparse_tag"]),
                    "Unsupported reparse point")
    return path


def selection_data(path):
    raw = Path(path).read_bytes()
    selection = json.loads(raw.decode("utf-8"))
    root = Path(selection["root"])
    require(root.is_absolute() and root.name.upper() == "FOREX", "Non-FOREX selection")
    documents = selection["documents"]
    require(len(documents) == 5, "Pilot requires exactly five documents")
    raw_inventory = Path(selection["inventory"]).read_bytes()
    require(digest(raw_inventory) == selection["inventory_sha256"], "Inventory changed")
    inventory = [json.loads(x) for x in raw_inventory.decode("utf-8").splitlines()]
    seen = set()
    for d in documents:
        relative = d["relative_path"].replace("\\", "/")
        p = safe_relative(relative)
        require(relative.casefold() not in seen, "Duplicate selected source")
        seen.add(relative.casefold())
        require(p.suffix.lower() == ".docx" and not p.name.startswith("~$"), "Not a DOCX source")
        matches = [r for r in inventory if r.get("relative_path", "").replace("\\", "/") == relative]
        require(len(matches) == 1 and matches[0].get("kind") == "document"
                and matches[0].get("extension") == ".docx", "Source not in manifest")
        require(all(d.get(k) == v for k, v in matches[0].items()), "Selection metadata differs")
        require(not d.get("skipped_link") and d.get("availability") == "metadata_local_candidate",
                "Unavailable source candidate")
    require(sum(d["size"] for d in documents) <= MAX_TOTAL
            and all(d["size"] <= MAX_SOURCE for d in documents), "Capture byte ceiling")
    return selection, digest(raw)


@contextmanager
def locked_source(path, expected):
    before = metadata(path)
    require(not before["attributes"] & (0x1000 | 0x40000 | 0x400000), "Source requires hydration")
    require(before["size"] <= MAX_SOURCE, "Source byte ceiling")
    require(all(before[k] == expected[k] for k in ("size", "mtime_ns")), "Source metadata changed")
    if os.name == "nt":
        import msvcrt
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                 ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        k.CreateFileW.restype = ctypes.c_void_p
        # Read-only, share-read only, OPEN_NO_RECALL, sequential scan.
        h = k.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x00100000 | 0x08000000, None)
        if h == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "Read-only no-recall capture open")
        f = os.fdopen(msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY), "rb")
    else:
        f = Path(path).open("rb")
    with f:
        yield f, before
        after = metadata(path)
        require(all(before[k] == after[k] for k in ("size", "mtime_ns")), "Source changed while held")


def read_source(f):
    data = f.read(MAX_SOURCE + 1)
    require(len(data) <= MAX_SOURCE, "Source byte ceiling")
    return data


def font_set():
    names = dict(regular="arial.ttf", bold="arialbd.ttf", italic="ariali.ttf", bold_italic="arialbi.ttf")
    return {k: ImageFont.truetype(str(Path("C:/Windows/Fonts") / n), FONT_SIZE) for k, n in names.items()}


def val(node, path, default=None, attr="val"):
    e = node.find(path)
    return e.get(W + attr, default) if e is not None else default


def text_of(node):
    result = []
    def walk(n):
        if n is not node and n.tag in (W+"tbl", W+"txbxContent"):
            return
        if n.tag == W+"t": result.append(n.text or "")
        elif n.tag in (W+"br", W+"cr"): result.append("\n")
        elif n.tag == W+"tab": result.append("\t")
        elif n.tag == W+"noBreakHyphen": result.append("\u2011")
        elif n.tag == W+"softHyphen": result.append("\u00ad")
        else:
            for c in n: walk(c)
    walk(node)
    return "".join(result)


def branch_signature(node):
    return dict(text=[(x.tag.split("}")[-1], x.text or "") for x in node.iter()
                      if x.tag in (W+"t", W+"br", W+"tab")],
                tables=[[(text_of(c), val(c, W+"tcPr/"+W+"gridSpan", "1"),
                          val(c, W+"tcPr/"+W+"vMerge", "absent")) for c in t.iter(W+"tc")]
                        for t in node.iter(W+"tbl")],
                images=[x.get(R+"embed") or x.get(R+"link") for x in node.iter(A+"blip")])


def collapse_alternatives(root, issues):
    aliases = []
    for parent in list(root.iter()):
        for node in list(parent):
            if node.tag != MC+"AlternateContent": continue
            choices = node.findall(MC+"Choice"); fallback = node.find(MC+"Fallback")
            if not choices or fallback is None:
                issues.append("Unsupported alternate-content branch"); continue
            chosen = choices[0]
            if any(branch_signature(b) != branch_signature(chosen) for b in [*choices[1:], fallback]):
                issues.append("Divergent alternate-content branches"); continue
            for branch in [*choices[1:], fallback]:
                aliases.extend(zip(branch.iter(W+"tbl"), chosen.iter(W+"tbl")))
            i = list(parent).index(node); parent.remove(node)
            for j, child in enumerate(list(chosen)): parent.insert(i+j, child)
    return aliases


def run_style(properties, inherited):
    style = dict(inherited)
    if properties is not None:
        for key, tag in (("bold", "b"), ("italic", "i"), ("underline", "u")):
            e = properties.find(W+tag)
            if e is not None: style[key] = e.get(W+"val", "1") not in ("0", "false", "off", "none")
        color = val(properties, W+"color")
        if color and re.fullmatch(r"[0-9a-fA-F]{6}", color): style["color"] = "#"+color
    return style


def paragraph_data(p, styles):
    default = dict(bold=False, italic=False, underline=False, color="#000000")
    inherited = run_style(styles.get(val(p, W+"pPr/"+W+"pStyle")), default)
    inherited = run_style(p.find(W+"pPr/"+W+"rPr"), inherited)
    runs = [dict(text=text_of(r), **run_style(r.find(W+"rPr"), inherited)) for r in p.findall(W+"r")]
    require("".join(r["text"] for r in runs) == text_of(p), "Unsupported wrapped table text")
    return dict(text=text_of(p), alignment=val(p, W+"pPr/"+W+"jc", "left"), runs=runs)


def table_data(table, table_id, part, styles, issues):
    grid = [max(1, int(x.get(W+"w", "1500"))) for x in table.findall(W+"tblGrid/"+W+"gridCol")]
    rows = table.findall(W+"tr"); require(rows, "Empty table")
    if len(list(table.iter(W+"tbl"))) > 1: issues.append("Unsupported nested table parent coverage")
    if any(x.tag in (W+"drawing", W+"pict", W+"object", W+"fldSimple") for x in table.iter()):
        issues.append("Unsupported drawing/object/field inside table")
    if any(x.tag in {W+t for t in ("hMerge", "textDirection", "vertAlign", "strike", "dstrike",
                                   "vanish", "highlight", "numPr", "bidi", "rtl")}
           for x in table.iter()):
        issues.append("Unsupported table text/merge styling")
    style = val(table, W+"tblPr/"+W+"tblStyle", "TableNormal")
    if style not in ("TableGrid", "TableNormal", "NormalTable"):
        issues.append("Unsupported conditional table style: "+style)
    def borders(node, path):
        return [dict(side=x.tag.split("}")[-1], **{k.split("}")[-1]:v for k,v in x.attrib.items()})
                for x in node.findall(path)]
    cells = []; active = {}
    for ri, row in enumerate(rows):
        require(val(row, W+"trPr/"+W+"gridBefore", "0") == "0"
                and val(row, W+"trPr/"+W+"gridAfter", "0") == "0", "Unsupported omitted grid cells")
        col = 0; new_active = {}
        for cell in row.findall(W+"tc"):
            span = int(val(cell, W+"tcPr/"+W+"gridSpan", "1")); require(span > 0, "Invalid merge")
            merge = cell.find(W+"tcPr/"+W+"vMerge")
            paragraphs = [paragraph_data(p, styles) for p in cell.findall(W+"p")]
            if any(p.find(W+"pPr/"+W+"numPr") is not None for p in cell.findall(W+"p")):
                issues.append("Unsupported automatic numbering inside table")
            raw = "\n".join(p["text"] for p in paragraphs)
            if merge is not None and merge.get(W+"val", "continue") == "continue":
                require(col in active and active[col]["colspan"] == span and not raw.strip(),
                        "Invalid/text-bearing vertical merge continuation")
                origin = active[col]; origin["rowspan"] += 1; new_active[col] = origin
            else:
                origin = dict(row=ri, column=col, rowspan=1, colspan=span, text=raw,
                              paragraphs=paragraphs, valign=val(cell, W+"tcPr/"+W+"vAlign", "top"),
                              shading=val(cell, W+"tcPr/"+W+"shd", "FFFFFF", "fill"),
                              borders=borders(cell, W+"tcPr/"+W+"tcBorders/*"))
                cells.append(origin)
                if merge is not None: new_active[col] = origin
            col += span
        active = new_active
        if not grid: grid = [1500]*col
        require(col == len(grid), "Row/grid mismatch")
    return dict(id=table_id, part=part, grid=grid, row_count=len(rows), cells=cells,
                table_style=style, borders=borders(table, W+"tblPr/"+W+"tblBorders/*"))


def custom_xml_metadata(package, xml, relationships, issues):
    """Recognize only the observed empty bibliography store and its schema props.

    Retain every original custom-XML byte privately, including relationships.
    Unknown/nonempty content, bindings or unexpected schemas remain blocking.
    """
    bibliography = "http://schemas.openxmlformats.org/officeDocument/2006/bibliography"
    ds = "{http://schemas.openxmlformats.org/officeDocument/2006/customXml}"
    names = [n for n in package.namelist() if n.startswith("customXml/") and not n.endswith("/")]
    recognized = set()
    for name in names:
        node = xml.get(name)
        if node is None or node.tag != "{"+bibliography+"}Sources":
            continue
        if (list(node) or "".join(node.itertext()).strip()
                or not set(node.attrib) <= {"SelectedStyle", "StyleName", "Version"}):
            continue
        props = [r for r in relationships if r["owner"] == name
                 and r.get("Type") == R[1:-1]+"/customXmlProps"
                 and r.get("TargetMode") != "External"]
        if len(props) != 1:
            continue
        prop_name = props[0].get("resolved")
        prop = xml.get(prop_name)
        if prop is None or prop.tag != ds+"datastoreItem" or set(prop.attrib) != {ds+"itemID"}:
            continue
        try:
            uuid.UUID(prop.get(ds+"itemID"))
        except (ValueError, TypeError, AttributeError):
            continue
        children = list(prop)
        if (len(children) != 1 or children[0].tag != ds+"schemaRefs"
                or children[0].attrib or "".join(prop.itertext()).strip()):
            continue
        refs = list(children[0])
        if (len(refs) != 1 or refs[0].tag != ds+"schemaRef" or list(refs[0])
                or refs[0].attrib != {ds+"uri": bibliography}):
            continue
        rel_name = props[0]["relationship_part"]
        owned_relationships = [r for r in relationships if r["relationship_part"] == rel_name]
        if len(owned_relationships) != 1:
            continue
        recognized.update((name, prop_name, rel_name))
    records = []
    for name in names:
        raw = package.read(name)
        supported = name in recognized
        records.append(dict(part=name, classification="empty_bibliography_metadata" if supported
                            else "unsupported_custom_xml", sha256=digest(raw),
                            original_bytes_base64=base64.b64encode(raw).decode("ascii")))
        if not supported:
            issues.append("Custom XML needs separate preservation review: "+name)
    if any(x.tag == W+"dataBinding" for root in xml.values() for x in root.iter()):
        issues.append("Custom XML data binding needs separate preservation review")
    return records


def parse_package(data):
    issues = []; tables = []; prose = []; media = []; relationships = []; occurrences = []; coverage = []; alternatives = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist(); names = [i.filename for i in infos]
        require(len(infos) <= 2000 and sum(i.file_size for i in infos) <= 128*1024**2, "Package expansion ceiling")
        require(len(names) == len(set(names)), "Duplicate package entries")
        for name in names: safe_relative(name.rstrip("/"))
        require("word/document.xml" in names, "Not Word OOXML")
        xml = {}
        for i in infos:
            if i.filename.endswith((".xml", ".rels")):
                require(i.file_size <= 16*1024**2, "XML ceiling")
                raw = z.read(i.filename)
                require(b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper(), "XML entities")
                xml[i.filename] = ET.fromstring(raw)
        for name, root in xml.items():
            if not name.endswith(".rels"): continue
            owner = "" if name == "_rels/.rels" else posixpath.join(posixpath.dirname(posixpath.dirname(name)), posixpath.basename(name)[:-5])
            for relation in root:
                r = dict(relation.attrib, owner=owner, relationship_part=name)
                if r.get("TargetMode") == "External": issues.append("Unresolved external relationship: "+r.get("Type", ""))
                else:
                    target = r.get("Target", "")
                    require("\\" not in target and not PureWindowsPath(target).drive and not target.startswith("/"), "Unsafe relationship target")
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(owner), target)); safe_relative(resolved)
                    r["resolved"] = resolved
                    if resolved not in names: issues.append("Missing relationship part: "+resolved)
                relationships.append(r)
        styles = {s.get(W+"styleId"): s.find(W+"rPr") for s in xml.get("word/styles.xml", ET.Element("empty")).findall(W+"style")}
        numbering = xml.get("word/numbering.xml", ET.Element("empty"))
        abstracts = {n.get(W+"abstractNumId"): n for n in numbering.findall(W+"abstractNum")}
        numbers = {n.get(W+"numId"): n for n in numbering.findall(W+"num")}
        def bullet_label(paragraph):
            num = paragraph.find(W+"pPr/"+W+"numPr")
            if num is None: return None
            num_id = val(num, W+"numId"); level = val(num, W+"ilvl", "0")
            if num_id == "0": return None
            instance = numbers.get(num_id)
            definition = abstracts.get(val(instance, W+"abstractNumId")) if instance is not None else None
            lvl = next((x for x in definition.findall(W+"lvl") if x.get(W+"ilvl") == level), None) if definition is not None else None
            if lvl is None or val(lvl, W+"numFmt") != "bullet" or instance.findall(W+"lvlOverride"):
                issues.append("Unsupported automatic prose numbering"); return None
            raw = val(lvl, W+"lvlText", "")
            font = val(lvl, W+"rPr/"+W+"rFonts", "", "ascii")
            label = {("Symbol", "\uf0b7"): "\u2022", ("Wingdings", "\uf0a7"): "\u25aa"}.get((font,raw),raw)
            if not label or any(0xE000 <= ord(c) <= 0xF8FF for c in label):
                issues.append("Unsupported private-use bullet glyph"); return None
            return dict(num_id=num_id, level=int(level), raw_label=raw, font=font, displayed_label=label)
        if any(n.startswith("word/embeddings/") or n.endswith("vbaProject.bin") for n in names): issues.append("Unsupported embedding/macro")
        metadata_parts = custom_xml_metadata(z, xml, relationships, issues)
        for name in sorted(n for n in names if n.startswith("word/media/")):
            raw = z.read(name)
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    image.load(); fmt, dimensions = image.format, image.size
                ext = {"PNG":".png", "JPEG":".jpg", "GIF":".gif", "TIFF":".tiff", "BMP":".bmp", "WEBP":".webp"}.get(fmt)
                require(ext, "Unknown image format")
            except Exception as e:
                ext, dimensions, fmt = Path(name).suffix or ".bin", None, None
                issues.append("Unsupported image "+name+": "+str(e))
            media.append(dict(part=name, bytes=raw, sha256=digest(raw), extension=ext, dimensions=dimensions, format=fmt))
        parts = sorted(n for n in xml if re.fullmatch(r"word/(document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml", n))
        parts.sort(key=lambda n:(n != "word/document.xml", n))
        for part in parts:
            root = xml[part]; raw_tables = list(root.iter(W+"tbl"))
            alternatives.extend(dict(part=part, branches=[dict(kind=b.tag.split("}")[-1], sha256=digest(ET.tostring(b)), signature=branch_signature(b)) for b in n]) for n in root.iter(MC+"AlternateContent"))
            aliases = collapse_alternatives(root, issues)
            for n in root.iter():
                if n.tag in (W+"object", W+"fldSimple", W+"fldChar", W+"instrText", W+"altChunk", W+"del", W+"ins", W+"sym", W+"commentReference", W+"footnoteReference", W+"endnoteReference"):
                    issues.append("Unsupported meaningful feature: "+n.tag.split("}")[-1])
                if n.tag == W+"drawing":
                    images, boxes = list(n.iter(A+"blip")), list(n.iter(W+"txbxContent"))
                    if not images and not boxes: issues.append("Unsupported drawing")
                    if boxes and any(x.get("prst") != "rect" for x in n.iter(A+"prstGeom")): issues.append("Unsupported text-box geometry")
                if n.tag == W+"pict": issues.append("Unsupported non-alternate VML drawing")
            active = list(root.iter(W+"tbl"))
            ids = {id(t):"table_%02d" % (len(tables)+i+1) for i,t in enumerate(active)}
            alias_map = {id(a):id(b) for a,b in aliases}
            for i,t in enumerate(raw_tables,1):
                target = alias_map.get(id(t),id(t)); require(target in ids, "Unmapped table branch")
                coverage.append(dict(part=part, raw_table=i, output_table=ids[target], alternate_alias=id(t) in alias_map))
            for t in active: tables.append(table_data(t, ids[id(t)], part, styles, issues))
            def walk(node, context):
                if node.tag == W+"tbl":
                    prose.append(dict(part=part, context=context, table=ids[id(node)])); return
                if node.tag == W+"p":
                    text = text_of(node)
                    if text.strip():
                        bullet = bullet_label(node)
                        prose.append(dict(part=part, context=context, text=text, numbering=bullet))
                    for j,box in enumerate(node.iter(W+"txbxContent"),1):
                        for c in box: walk(c, context+"/textbox_%d" % j)
                    return
                for c in node: walk(c,context)
            walk(root,part)
            for n in root.iter(A+"blip"):
                rid = n.get(R+"embed") or n.get(R+"link")
                matches = [r for r in relationships if r["owner"] == part and r.get("Id") == rid]
                occurrences.append(dict(part=part, relationship=rid, targets=[r.get("resolved",r.get("Target")) for r in matches]))
                if len(matches) != 1: issues.append("Unresolved image occurrence")
        if "word/comments.xml" in xml: issues.append("Comments need annotation-location coverage")
        for m in media:
            m["relationships"] = [r for r in relationships if r.get("resolved") == m["part"]]
            m["occurrences"] = [x for x in occurrences if m["part"] in x["targets"]]
        for relation in relationships:
            if relation.get("Type", "").endswith("/image") and relation.get("resolved") not in {m["part"] for m in media}:
                issues.append("Image relationship not covered by extracted media")
    return dict(tables=tables, media=media, prose=prose, relationships=relationships,
                image_occurrences=occurrences, table_coverage=coverage, raw_table_count=len(coverage),
                alternate_content=alternatives, metadata_parts=metadata_parts,
                package_parts=[dict(name=i.filename,size=i.file_size) for i in infos], issues=sorted(set(issues)))


def font_for(run, fonts):
    return fonts["bold_italic" if run["bold"] and run["italic"] else "bold" if run["bold"] else "italic" if run["italic"] else "regular"]


def layout_paragraph(p, width, fonts):
    lines = [[]]; widths = [0.0]
    for run in p["runs"]:
        font = font_for(run, fonts); missing = bytes(font.getmask("\uffff"))
        for ch in run["text"]:
            if ch == "\n": lines.append([]); widths.append(0.0); continue
            visible = "    " if ch == "\t" else ch
            require(ch.isspace() or bytes(font.getmask(ch)) != missing, "Missing font glyph")
            box = font.getbbox(visible, anchor="ls")
            advance = max(float(font.getlength(visible)), box[2]-box[0])
            require(advance <= width, "Glyph exceeds cell")
            if lines[-1] and widths[-1]+advance > width:
                split = next((i+1 for i in range(len(lines[-1])-1,-1,-1) if lines[-1][i]["character"].isspace()),len(lines[-1]))
                carry = lines[-1][split:]; lines[-1] = lines[-1][:split]
                widths[-1] = sum(g["advance"] for g in lines[-1]); lines.append(carry); widths.append(sum(g["advance"] for g in carry))
                if widths[-1]+advance > width: lines.append([]); widths.append(0.0)
            lines[-1].append(dict(character=ch, visible=visible, advance=advance, style=run)); widths[-1] += advance
    require("".join(g["character"] for line in lines for g in line) == p["text"].replace("\n",""), "Wrapped text mismatch")
    return [dict(glyphs=line,width=widths[i]) for i,line in enumerate(lines)]


def render_table(table, destination, fonts):
    widths = [max(160,min(600,math.ceil(x*0.14))) for x in table["grid"]]
    heights = [44]*table["row_count"]; layouts = []
    for c in table["cells"]:
        width = sum(widths[c["column"]:c["column"]+c["colspan"]])-28
        paragraphs = [dict(alignment=p["alignment"], lines=layout_paragraph(p,width,fonts)) for p in c["paragraphs"]]
        needed = sum(max(1,len(p["lines"]))*30+4 for p in paragraphs)+24
        available = sum(heights[c["row"]:c["row"]+c["rowspan"]])
        if needed > available: heights[c["row"]+c["rowspan"]-1] += needed-available
        layouts.append((c,paragraphs,needed))
    xs=[8]; ys=[8]
    for x in widths: xs.append(xs[-1]+x)
    for y in heights: ys.append(ys[-1]+y)
    size=(xs[-1]+8,ys[-1]+8); require(size[0]*size[1] <= MAX_PIXELS,"Table exceeds 20 megapixels")
    image=Image.new("RGB",size,"white"); draw=ImageDraw.Draw(image); measured=[]
    for c,paragraphs,needed in layouts:
        x0,x1=xs[c["column"]],xs[c["column"]+c["colspan"]]; y0,y1=ys[c["row"]],ys[c["row"]+c["rowspan"]]
        shade=c["shading"]; require(shade=="auto" or re.fullmatch(r"[0-9a-fA-F]{6}",shade),"Unsupported shading")
        draw.rectangle((x0,y0,x1,y1),fill="white" if shade=="auto" else "#"+shade)
        default=dict(val="single",color="000000",sz="4") if table["table_style"]=="TableGrid" else dict(val="nil")
        sides={k:default for k in ("top","bottom","left","right")}
        for b in table["borders"]:
            for side in (("top","bottom") if b["side"]=="insideH" else ("left","right") if b["side"]=="insideV" else (b["side"],)): sides[side]=b
        for b in c["borders"]: sides[b["side"]]=b
        require(set(sides) <= {"top","bottom","left","right"},"Unsupported diagonal cell border")
        for side,edge in dict(top=(x0,y0,x1,y0),bottom=(x0,y1,x1,y1),left=(x0,y0,x0,y1),right=(x1,y0,x1,y1)).items():
            b=sides[side]; kind=b.get("val","single")
            if kind in ("nil","none"): continue
            require(kind=="single","Unsupported border pattern")
            color=b.get("color","000000"); color="000000" if color=="auto" else color
            require(re.fullmatch(r"[0-9a-fA-F]{6}",color),"Unsupported border color")
            draw.line(edge,fill="#"+color,width=max(1,round(int(b.get("sz","4"))/6)))
        y=y0+12
        if c["valign"]=="center": y+=max(0,(y1-y0-needed)/2)
        elif c["valign"]=="bottom": y+=max(0,y1-y0-needed)
        boxes=[]
        for p in paragraphs:
            require(p["alignment"] in ("left","start","center","right","end"),"Unsupported alignment")
            for line in p["lines"]:
                extra=max(0,x1-x0-28-line["width"])
                x=x0+14+(extra/2 if p["alignment"]=="center" else extra if p["alignment"] in ("right","end") else 0)
                for g in line["glyphs"]:
                    run=g["style"]; font=font_for(run,fonts)
                    box=draw.textbbox((x,y+20),g["visible"],font=font,anchor="ls")
                    require(box[0]>=x0+5 and box[2]<=x1-5 and box[1]>=y0+3 and box[3]<=y1-3,"Glyph escapes cell bounds")
                    draw.text((x,y+20),g["visible"],font=font,fill=run["color"],anchor="ls")
                    if run["underline"]: draw.line((x,y+23,x+g["advance"],y+23),fill=run["color"],width=1)
                    boxes.append(dict(character=g["character"],box=box)); x+=g["advance"]
                y+=30
            y+=4
        measured.append(dict(row=c["row"],column=c["column"],rectangle=[x0,y0,x1,y1],glyphs=boxes,exact_text=c["text"]))
    image.save(destination,format="PNG")
    with Image.open(destination) as check: check.load(); require(check.size==size and check.format=="PNG","PNG validation")
    return dict(size=size,pixels=size[0]*size[1],font_pixels=FONT_SIZE,cells=measured,sha256=digest(Path(destination).read_bytes()))


def output_prefix(document):
    relative=document["relative_path"].replace("\\","/")
    stem=re.sub(r"[^A-Za-z0-9._-]+","_",PurePosixPath(relative).stem).strip("._") or "document"
    return stem[:48]+"_"+digest(relative.encode("utf-8"))[:12]


def recover_copy(data, document, directory, fonts):
    parsed=parse_package(data); outputs=[]; prefix=output_prefix(document)
    for i,m in enumerate(parsed["media"],1):
        name=prefix+"_picture_%02d"%i+m["extension"]; path=directory/name
        with path.open("xb") as f: f.write(m.pop("bytes"))
        require(digest(path.read_bytes())==m["sha256"],"Embedded bytes changed")
        outputs.append(dict(name=name,kind="picture",private=str(path),sha256=m["sha256"],part=m["part"],dimensions=m["dimensions"]))
    for t in parsed["tables"]:
        name=prefix+"_"+t["id"]+".png"; path=directory/name
        try:
            t["rendering"]=render_table(t,path,fonts)
            outputs.append(dict(name=name,kind="table",private=str(path),sha256=t["rendering"]["sha256"],table_id=t["id"],size=t["rendering"]["size"]))
        except RecoveryError as e: parsed["issues"].append(t["id"]+": "+str(e))
    if any("text" in x for x in parsed["prose"]):
        name=prefix+"_text.txt"; path=directory/name; chunks=[]; context=None
        for item in parsed["prose"]:
            if item["context"]!=context: chunks.append("\n["+item["context"]+"]\n"); context=item["context"]
            label = item.get("numbering")
            chunks.append(("  "*label["level"]+label["displayed_label"]+"\t" if label else "") + item.get("text","["+item.get("table","")+"]"))
        with path.open("x",encoding="utf-8",newline="") as f: f.write("\n".join(chunks))
        outputs.append(dict(name=name,kind="text",private=str(path),sha256=digest(path.read_bytes())))
    if "known_tables" in document: require(parsed["raw_table_count"]==document["known_tables"],"Known sample table count differs")
    if "known_media" in document: require(len(parsed["media"])==document["known_media"],"Known sample media count differs")
    return dict(source=document,source_sha256=digest(data),outputs=outputs,coverage=parsed,complete=not parsed["issues"])


def prepare(selection_path, output, saved_captures=None):
    output=Path(output); require(output.is_absolute() and output.is_dir(),"Explicit existing evidence directory required")
    with (output/"prepare.started").open("x") as f: f.write("one attempt\n")
    selection,selection_hash=selection_data(selection_path); save(output/"selection.validated.json",selection)
    previous = None
    origin = None
    if saved_captures is not None:
        saved_captures = Path(saved_captures)
        require(saved_captures.is_absolute() and saved_captures.resolve() != output.resolve(),
                "Saved captures require a distinct absolute evidence directory")
        previous = load(saved_captures/"preparation.json")
        require(previous["selection_sha256"] == selection_hash
                and previous["source_count"] == 5
                and [r["source"] for r in previous["documents"]] == selection["documents"],
                "Saved capture selection differs")
        origin = dict(evidence=str(saved_captures),
                      preparation_sha256=digest((saved_captures/"preparation.json").read_bytes()),
                      previous_tool_sha256=previous["tool_sha256"])
    fonts=font_set(); results=[]
    save(output/"preparation.json",dict(status="incomplete",selection_sha256=selection_hash,documents=results))
    for i,d in enumerate(selection["documents"],1):
        directory=output/("document_%02d"%i); directory.mkdir()
        if previous is not None:
            old = previous["documents"][i-1]
            saved = Path(old["capture"])
            require(saved.resolve().is_relative_to(saved_captures.resolve()), "Saved capture escapes evidence")
            data = saved.read_bytes()
            before, after = old["before"], old["after"]
            require(len(data) <= MAX_SOURCE and digest(data) == old["source_sha256"]
                    and all(before[k] == after[k] == d[k] for k in ("size", "mtime_ns"))
                    and len(data) == before["size"], "Saved capture/provenance changed")
        else:
            path=safe_path(selection["root"],d["relative_path"].replace("\\","/"))
            with locked_source(path,d) as (f,before):
                data=read_source(f); require(len(data)==before["size"],"Capture size mismatch")
                after=metadata(path)
        if d.get("known_sha256"): require(digest(data)==d["known_sha256"],"Known sample hash changed")
        capture=directory/"captured.docx"
        with capture.open("xb") as dest: dest.write(data); dest.flush(); os.fsync(dest.fileno())
        result=recover_copy(data,d,directory,fonts); result.update(capture=str(capture),before=before,after=after)
        if previous is not None:
            require({o["name"]:(o["kind"],o["sha256"]) for o in result["outputs"]} ==
                    {o["name"]:(o["kind"],o["sha256"]) for o in old["outputs"]},
                    "Saved-copy outputs differ from previously prepared bytes")
        for item in result["outputs"]:
            relative=str(PurePosixPath(d["relative_path"].replace("\\","/")).parent/item["name"])
            if previous is not None:
                # No source/root metadata access during saved-copy preparation.
                target=Path(selection["root"]).joinpath(*safe_relative(relative).parts)
            else:
                target=safe_path(selection["root"],relative); require(not target.exists(),"Existing output collision")
            item.update(relative_destination=relative,destination=str(target))
        save(directory/"provenance.json",result); results.append(result)
        save(output/"preparation.json",dict(status="incomplete",selection_sha256=selection_hash,documents=results))
    result=dict(status="complete" if all(r["complete"] for r in results) else "incomplete",selection_sha256=selection_hash,documents=results,source_count=len(results),source_bytes=sum(r["before"]["size"] for r in results),deletion_eligible=False,tool_sha256=digest(Path(__file__).read_bytes()),saved_capture_origin=origin,source_captures=0 if previous is not None else len(results))
    save(output/"preparation.json",result); return result


def validate_prepared(selection_path, output):
    output=Path(output); selection,selection_hash=selection_data(selection_path); prepared=load(output/"preparation.json")
    require(prepared["status"]=="complete" and prepared["source_count"]==5,"Incomplete preparation cannot publish")
    require(prepared["selection_sha256"]==selection_hash and prepared["tool_sha256"]==digest(Path(__file__).read_bytes()),"Selection/tool changed")
    require([r["source"] for r in prepared["documents"]]==selection["documents"],"Prepared selection differs")
    destinations=set()
    for result in prepared["documents"]:
        require(result["complete"] and not result["coverage"]["issues"],"Unsupported content")
        capture=Path(result["capture"])
        require(capture.resolve().is_relative_to(output.resolve()) and digest(capture.read_bytes())==result["source_sha256"],"Capture hash mismatch")
        c=result["coverage"]; ids={t["id"] for t in c["tables"]}
        with zipfile.ZipFile(capture) as package:
            custom_names = {n for n in package.namelist() if n.startswith("customXml/") and not n.endswith("/")}
            require(custom_names == {m["part"] for m in c["metadata_parts"]}, "Custom XML provenance gap")
            for m in c["metadata_parts"]:
                original = package.read(m["part"])
                require(m["classification"] == "empty_bibliography_metadata"
                        and digest(original) == m["sha256"]
                        and base64.b64decode(m["original_bytes_base64"], validate=True) == original,
                        "Metadata preservation mismatch")
        require(len(c["table_coverage"])==c["raw_table_count"] and ids=={m["output_table"] for m in c["table_coverage"]},"Table coverage gap")
        require(ids=={o["table_id"] for o in result["outputs"] if o["kind"]=="table"},"Missing table output")
        require({m["part"] for m in c["media"]}=={o["part"] for o in result["outputs"] if o["kind"]=="picture"},"Missing image output")
        for item in result["outputs"]:
            private=Path(item["private"])
            require(private.resolve().is_relative_to(output.resolve()) and digest(private.read_bytes())==item["sha256"],"Prepared output changed")
            relative=item["relative_destination"]; p=safe_relative(relative)
            parent=PurePosixPath(result["source"]["relative_path"].replace("\\","/")).parent
            require(p.parent==parent and p.name==item["name"],"Output escapes source folder")
            require(relative.casefold() not in destinations,"Duplicate destination"); destinations.add(relative.casefold())
            if item["kind"]!="text":
                with Image.open(private) as image:
                    image.load()
                    if item["kind"]=="table": require(image.width*image.height<=MAX_PIXELS,"Table image ceiling")
    return selection,prepared


def publish(selection_path, output, review_path):
    output=Path(output)
    with (output/"publish.started").open("x") as f: f.write("one attempt\n")
    selection,prepared=validate_prepared(selection_path,output); review=load(review_path)
    require(review.get("approved") is True and review["preparation_sha256"]==digest((output/"preparation.json").read_bytes()) and review["selection_sha256"]==prepared["selection_sha256"],"Exact preparation requires private/visual approval")
    ledger=dict(status="incomplete",selection_sha256=prepared["selection_sha256"],source_checks=[],published=[],staged=[],deletion_eligible=False)
    save(output/"publication.json",ledger)
    with ExitStack() as stack:
        for result in prepared["documents"]:
            path=safe_path(selection["root"],result["source"]["relative_path"].replace("\\","/"))
            f,before=stack.enter_context(locked_source(path,result["source"])); require(digest(read_source(f))==result["source_sha256"],"Source hash changed")
            ledger["source_checks"].append(dict(source=str(path),before=before,sha256=result["source_sha256"]))
        targets=[]
        for result in prepared["documents"]:
            for item in result["outputs"]:
                target=safe_path(selection["root"],item["relative_destination"])
                require(str(target)==item["destination"] and not target.exists(),"Output destination changed/collision")
                targets.append((item,target))
        save(output/"publication.json",ledger)
        for item,target in targets:
            stage=target.with_name("."+target.name+"."+uuid.uuid4().hex+".recovering")
            ledger["staged"].append(str(stage)); save(output/"publication.json",ledger)
            with stage.open("xb") as f: f.write(Path(item["private"]).read_bytes()); f.flush(); os.fsync(f.fileno())
            require(digest(stage.read_bytes())==item["sha256"],"Staged hash mismatch")
            if os.name=="nt": os.rename(stage,target)  # Windows rename never replaces an existing file.
            else: os.link(stage,target); stage.unlink()
            require(digest(target.read_bytes())==item["sha256"],"Published hash mismatch")
            ledger["published"].append(dict(path=str(target),sha256=item["sha256"],kind=item["kind"],status="verified")); save(output/"publication.json",ledger)
        for check in ledger["source_checks"]:
            check["after"]=metadata(check["source"])
            require(all(check["before"][k]==check["after"][k] for k in ("size","mtime_ns")),"Source changed during publication")
    ledger["status"]="complete"; save(output/"publication.json",ledger); return ledger


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage",choices=("prepare","preflight","publish","verify"),default="prepare")
    parser.add_argument("--selection",type=Path); parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--saved-captures",type=Path,help="Prepare from exact-selection verified private copies without source access")
    parser.add_argument("--review",type=Path); parser.add_argument("--supervise",action="store_true")
    args=parser.parse_args(argv)
    require(args.output.is_absolute() and args.output.is_dir(),"Absolute existing evidence directory required")
    if args.supervise:
        timeout={"preflight":30,"prepare":180,"publish":60,"verify":60}[args.stage]
        command=[sys.executable,str(Path(__file__).resolve()),"--stage",args.stage,"--output",str(args.output)]
        if args.selection: command += ["--selection",str(args.selection.resolve())]
        if args.saved_captures: command += ["--saved-captures",str(args.saved_captures.resolve())]
        if args.review: command += ["--review",str(args.review.resolve())]
        start=time.monotonic()
        try:
            r=subprocess.run(command,timeout=timeout,capture_output=True,text=True)
            outer=dict(returncode=r.returncode,elapsed_seconds=time.monotonic()-start,timeout_seconds=timeout,stdout=r.stdout,stderr=r.stderr)
        except subprocess.TimeoutExpired:
            outer=dict(returncode=124,status="timeout_incomplete",elapsed_seconds=time.monotonic()-start,timeout_seconds=timeout)
        save(args.output/(args.stage+"_outer.json"),outer); print(json.dumps(outer)); return outer["returncode"]
    try:
        require(args.saved_captures is None or args.stage == "prepare", "Saved captures apply only to preparation")
        if args.stage=="preflight":
            fonts=font_set()
            with (args.output/"preflight.started").open("x") as f: f.write("one preflight\n")
            save(args.output/"preflight.json",dict(dummy="FOREX_7B1_HOST_STORAGE_V1",output=str(args.output.resolve()),executable=str(Path(sys.executable).resolve()),pid=os.getpid(),tool=str(Path(__file__).resolve()),tool_sha256=digest(Path(__file__).read_bytes()),pillow=PILLOW_VERSION,fonts={k:v.path for k,v in fonts.items()},font_pixels=FONT_SIZE,source_access=False))
        else:
            require(args.selection is not None,"Exact selection required")
            if args.stage=="prepare": require(prepare(args.selection,args.output,args.saved_captures)["status"]=="complete","Preparation contains unsupported content")
            elif args.stage=="verify":
                _,result=validate_prepared(args.selection,args.output)
                save(args.output/"private_checks.json",dict(status="passed",preparation_sha256=digest((args.output/"preparation.json").read_bytes()),selection_sha256=result["selection_sha256"],counts={k:sum(o["kind"]==k for r in result["documents"] for o in r["outputs"]) for k in ("picture","table","text")}))
            else:
                require(args.review is not None,"Private/visual review required"); publish(args.selection,args.output,args.review)
        print(json.dumps(dict(stage=args.stage,status="complete",evidence=str(args.output)))); return 0
    except Exception as error:
        save(args.output/(args.stage+"_failure.json"),dict(status="incomplete",error=repr(error),stage=args.stage)); raise


if __name__ == "__main__":
    raise SystemExit(main())
