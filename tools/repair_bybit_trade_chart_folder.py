"""Dry-run-first repair for the single accepted Bybit Demo C377 chart folder.

The repair moves the existing chart directory from its stale month to the
accepted trade month and changes only that row's existing OOXML hyperlink.
It deliberately does not rebuild or save the workbook through openpyxl.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.master_journal_workbook import (
    _TRADE_FOLDER_INDEX_CACHE,
    _select_trade_chart_root,
    _trade_log_data_start_row,
    _trade_log_header_map,
)


EXPECTED_ROW_ID = "bybit:demo:trade:BTCUSDT:cd7d7d60e40d420e"
EXPECTED_TRADE_NUMBER = "C377"
EXPECTED_ACCOUNT = "Bybit Demo"
EXPECTED_SYMBOL = "BTCUSDT"
EXPECTED_OPEN = datetime(2026, 10, 4, 13, 7, 22)
EXPECTED_CLOSE = datetime(2026, 10, 7, 12, 1, 7)
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_Q = lambda namespace, name: f"{{{namespace}}}{name}"


class RepairRefused(RuntimeError):
    """A precondition or verification failed without authorizing broader edits."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _file_uri_path(uri: str) -> str:
    parsed = urlparse(str(uri or ""))
    if parsed.scheme.casefold() != "file" or parsed.netloc not in ("", "localhost"):
        raise RepairRefused("C377's existing hyperlink is not a local file URI")
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    return path.replace("/", "\\").rstrip("\\").casefold()


def _same_path_uri(uri: str, path: Path) -> bool:
    try:
        return _file_uri_path(uri) == str(path.resolve()).rstrip("\\").casefold()
    except (OSError, RepairRefused):
        return False


def _workbook_row(workbook_path: Path) -> dict[str, Any]:
    wb = load_workbook(workbook_path, read_only=True, data_only=False)
    try:
        if "Trade Log" not in wb.sheetnames:
            raise RepairRefused("Trading Journal has no Trade Log sheet")
        ws = wb["Trade Log"]
        headers = _trade_log_header_map(ws)
        required = ("Trade Number", "Row ID", "Account", "Symbol", "Open Time", "Close Time")
        if any(name not in headers for name in required):
            raise RepairRefused("Trade Log is missing a required C377 identity/date column")
        matches = []
        start_row = _trade_log_data_start_row(ws)
        for row_num, values in enumerate(ws.iter_rows(min_row=start_row, values_only=True), start=start_row):
            row_id_index = headers["Row ID"] - 1
            row_id = str(values[row_id_index] if row_id_index < len(values) else "").strip()
            if row_id != EXPECTED_ROW_ID:
                continue
            def cell_value(header: str):
                index = headers[header] - 1
                return values[index] if index < len(values) else None
            matches.append({
                "row": row_num,
                "trade_number": str(cell_value("Trade Number") or "").strip(),
                "row_id": row_id,
                "account": str(cell_value("Account") or "").strip(),
                "symbol": str(cell_value("Symbol") or "").strip(),
                "open_time": cell_value("Open Time"),
                "close_time": cell_value("Close Time"),
                "trade_number_column": headers["Trade Number"],
            })
        if len(matches) != 1:
            raise RepairRefused(f"Expected exactly one stable C377 row; found {len(matches)}")
        item = matches[0]
        if (item["trade_number"], item["account"], item["symbol"]) != (
            EXPECTED_TRADE_NUMBER, EXPECTED_ACCOUNT, EXPECTED_SYMBOL
        ):
            raise RepairRefused("Stable row ID does not match C377 / Bybit Demo / BTCUSDT")
        if item["open_time"] != EXPECTED_OPEN or item["close_time"] != EXPECTED_CLOSE:
            raise RepairRefused("Saved C377 dates do not match the accepted October timestamps")
        return item
    finally:
        wb.close()


def _json_row(json_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RepairRefused(f"Could not read the journal JSON trade source: {exc}") from exc
    rows = payload.get("items", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RepairRefused("Journal JSON does not contain an items list")
    matches = [r for r in rows if isinstance(r, dict) and str(r.get("id") or r.get("row_id") or "").strip() == EXPECTED_ROW_ID]
    if len(matches) != 1:
        raise RepairRefused(f"Expected exactly one stable C377 JSON row; found {len(matches)}")
    row = matches[0]
    opened = datetime.fromisoformat(str(row.get("open_time") or "")).replace(tzinfo=None)
    closed = datetime.fromisoformat(str(row.get("close_time") or "")).replace(tzinfo=None)
    if (
        str(row.get("trade_number") or "").strip() != EXPECTED_TRADE_NUMBER
        or str(row.get("account") or row.get("account_label") or "").strip() != EXPECTED_ACCOUNT
        or str(row.get("symbol") or "").strip() != EXPECTED_SYMBOL
        or opened != EXPECTED_OPEN
        or closed != EXPECTED_CLOSE
    ):
        raise RepairRefused("Journal JSON row does not match the accepted C377 identity and October dates")
    return {"row_id": EXPECTED_ROW_ID, "trade_number": EXPECTED_TRADE_NUMBER, "account": EXPECTED_ACCOUNT,
            "symbol": EXPECTED_SYMBOL, "open_time": opened.isoformat(), "close_time": closed.isoformat()}


def _worksheet_parts(zf: zipfile.ZipFile) -> tuple[str, str]:
    workbook_xml = ET.fromstring(zf.read("xl/workbook.xml"))
    rels_xml = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rels = {r.attrib["Id"]: r.attrib["Target"] for r in rels_xml.findall(_Q(PKG_REL_NS, "Relationship"))}
    sheet = next((s for s in workbook_xml.findall(f"{_Q(MAIN_NS, 'sheets')}/{_Q(MAIN_NS, 'sheet')}") if s.attrib.get("name") == "Trade Log"), None)
    if sheet is None:
        raise RepairRefused("Trading Journal package has no Trade Log worksheet")
    rid = sheet.attrib.get(_Q(DOC_REL_NS, "id"), "")
    target = rels.get(rid)
    if not target:
        raise RepairRefused("Trade Log worksheet relationship is missing")
    sheet_part = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
    sheet_part = str(PurePosixPath(sheet_part))
    rel_part = str(PurePosixPath(sheet_part).parent / "_rels" / (PurePosixPath(sheet_part).name + ".rels"))
    if rel_part not in zf.namelist():
        raise RepairRefused("Trade Log has no worksheet relationship part for its existing hyperlink")
    return sheet_part, rel_part


def _hyperlink_info(workbook_path: Path, row: dict[str, Any]) -> dict[str, Any]:
    cell_ref = f"{get_column_letter(row['trade_number_column'])}{row['row']}"
    with zipfile.ZipFile(workbook_path, "r") as zf:
        sheet_part, rel_part = _worksheet_parts(zf)
        sheet_root = ET.fromstring(zf.read(sheet_part))
        links = [h for h in sheet_root.findall(f"{_Q(MAIN_NS, 'hyperlinks')}/{_Q(MAIN_NS, 'hyperlink')}") if h.attrib.get("ref") == cell_ref]
        if len(links) != 1:
            raise RepairRefused(f"Expected one existing hyperlink on C377 cell {cell_ref}; found {len(links)}")
        rel_id = links[0].attrib.get(_Q(DOC_REL_NS, "id"), "")
        if not rel_id:
            raise RepairRefused("C377's existing hyperlink is not backed by an external relationship")
        rel_root = ET.fromstring(zf.read(rel_part))
        rels = [r for r in rel_root.findall(_Q(PKG_REL_NS, "Relationship")) if r.attrib.get("Id") == rel_id]
        if len(rels) != 1 or rels[0].attrib.get("TargetMode") != "External":
            raise RepairRefused("C377's existing hyperlink relationship is missing or not external")
        if not str(rels[0].attrib.get("Type", "")).endswith("/hyperlink"):
            raise RepairRefused("C377's existing relationship is not a hyperlink")
        uses = sum(1 for h in sheet_root.findall(f"{_Q(MAIN_NS, 'hyperlinks')}/{_Q(MAIN_NS, 'hyperlink')}") if h.attrib.get(_Q(DOC_REL_NS, "id")) == rel_id)
        return {"cell_ref": cell_ref, "relationship_id": rel_id, "relationship_uses": uses,
                "target": rels[0].attrib.get("Target", ""), "sheet_part": sheet_part, "rels_part": rel_part}


def _inventory(folder: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(folder.rglob("*"), key=lambda p: p.relative_to(folder).as_posix().casefold()):
        rel = path.relative_to(folder).as_posix()
        if path.is_symlink():
            raise RepairRefused(f"Refusing to move chart folder containing a symbolic link: {rel}")
        if path.is_dir():
            result.append({"relative_path": rel, "type": "directory", "size": None, "sha256": None})
        elif path.is_file():
            result.append({"relative_path": rel, "type": "file", "size": path.stat().st_size, "sha256": _sha256(path)})
        else:
            raise RepairRefused(f"Refusing to move unsupported chart-folder entry: {rel}")
    return result


def _root_for(chart_root: Path | None) -> tuple[Path, dict[str, Any]]:
    if chart_root is not None:
        return chart_root.expanduser().resolve(), {"selection": "explicit_test_or_cli_root", "root": str(chart_root.expanduser().resolve())}
    root, diagnostic = _select_trade_chart_root("C")
    if root is None:
        raise RepairRefused(f"Could not resolve configured local CRYPTO chart root: {diagnostic}")
    return Path(root).expanduser().resolve(), diagnostic


def _build_plan(workbook_path: Path, json_path: Path, chart_root: Path | None, recovery_root: Path) -> dict[str, Any]:
    workbook_path = workbook_path.expanduser().resolve()
    json_path = json_path.expanduser().resolve()
    root, root_selection = _root_for(chart_root)
    workbook_row = _workbook_row(workbook_path)
    json_row = _json_row(json_path)
    if workbook_row["row_id"] != json_row["row_id"]:
        raise RepairRefused("Workbook and JSON identify different C377 trades")
    source = root / "2026" / "APRIL" / EXPECTED_TRADE_NUMBER
    destination = root / "2026" / "OCTOBER" / EXPECTED_TRADE_NUMBER
    info = _hyperlink_info(workbook_path, workbook_row)
    if source.is_dir() and not destination.exists():
        if not _same_path_uri(info["target"], source):
            raise RepairRefused("C377's existing workbook link does not point to the verified APRIL source folder")
        action = "move_folder_and_retarget_existing_link"
        contents = _inventory(source)
    elif not source.exists() and destination.is_dir():
        if not _same_path_uri(info["target"], destination):
            raise RepairRefused("OCTOBER/C377 exists but the workbook link does not point to it; refusing to guess")
        action = "verified_no_op"
        contents = _inventory(destination)
    elif source.exists() and destination.exists():
        raise RepairRefused("Both APRIL/C377 and OCTOBER/C377 exist; refusing to merge, overwrite or duplicate")
    else:
        raise RepairRefused("Expected C377 source/destination state is incomplete or ambiguous")
    if not contents:
        raise RepairRefused("C377 chart folder is empty")
    workbook_hash = _sha256(workbook_path)
    return {
        "action": action,
        "trade": {"row_id": EXPECTED_ROW_ID, "trade_number": EXPECTED_TRADE_NUMBER,
                  "account": EXPECTED_ACCOUNT, "symbol": EXPECTED_SYMBOL,
                  "open_time": EXPECTED_OPEN.isoformat(), "close_time": EXPECTED_CLOSE.isoformat(),
                  "workbook_row": workbook_row["row"], "hyperlink_cell": info["cell_ref"]},
        "workbook_path": str(workbook_path), "workbook_sha256": workbook_hash,
        "json_path": str(json_path), "chart_root": str(root), "chart_root_selection": root_selection,
        "source_folder": str(source), "destination_folder": str(destination),
        "existing_link": info["target"], "existing_relationship_id": info["relationship_id"],
        "existing_relationship_uses": info["relationship_uses"],
        "new_link": destination.resolve().as_uri(), "inventory": contents,
        "recovery_root": str(recovery_root.expanduser().resolve()),
    }


def plan_digest(plan: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(plan)).hexdigest()


def _can_open_exclusively(path: Path) -> bool:
    """Test current OS sharing/lock state without changing file contents."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel.CreateFileW
        create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create_file.restype = wintypes.HANDLE
        handle = create_file(str(path), 0x80000000, 0, None, 3, 0x80, None)
        invalid = wintypes.HANDLE(-1).value
        if handle == invalid:
            error = ctypes.get_last_error()
            if error in {5, 32, 33}:
                return False
            raise OSError(error, ctypes.FormatError(error), str(path))
        kernel.CloseHandle(handle)
        return True
    handle = path.open("rb")
    try:
        import fcntl
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        return True
    finally:
        handle.close()


def _assert_workbook_available(path: Path) -> None:
    owner_file = path.with_name("~$" + path.name)
    if owner_file.exists() and not _can_open_exclusively(owner_file):
        raise RepairRefused(f"Excel owner file exists; close the workbook before repair: {owner_file}")
    if not os.access(path, os.R_OK | os.W_OK) or not _can_open_exclusively(path):
        raise RepairRefused("Trading Journal workbook is not readable and writable")


def _acquire_existing_gate(path: Path) -> Callable[[], None]:
    from render import master_service
    result = master_service._reserve_master_journal_workbook_sync(
        path, f"chart-folder-repair-{uuid.uuid4().hex[:12]}", "trade_folder_repair", wait_seconds=1.0,
    )
    if result is not None:
        raise RepairRefused("Journal workbook transaction is busy; no repair changes were made")
    return master_service._release_master_journal_workbook_sync


def _copy_backup(plan: dict[str, Any], recovery_root: Path) -> tuple[Path, Path, Path]:
    recovery_root.mkdir(parents=True, exist_ok=True)
    backup_dir = recovery_root / f"C377-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    backup_dir.mkdir()
    workbook_backup = backup_dir / "Trading Journal.xlsx"
    folder_backup = backup_dir / "C377"
    shutil.copy2(plan["workbook_path"], workbook_backup)
    shutil.copytree(plan["source_folder"], folder_backup, symlinks=True)
    if _sha256(workbook_backup) != plan["workbook_sha256"]:
        raise RepairRefused("Workbook backup hash does not match the planned source bytes")
    if _inventory(folder_backup) != plan["inventory"]:
        raise RepairRefused("C377 backup inventory does not match the planned source contents")
    manifest = {
        "trade_row_id": EXPECTED_ROW_ID,
        "trade_number": EXPECTED_TRADE_NUMBER,
        "source_folder": plan["source_folder"],
        "destination_folder": plan["destination_folder"],
        "workbook_path": plan["workbook_path"],
        "workbook_sha256": plan["workbook_sha256"],
        "folder_inventory": plan["inventory"],
        "backup_workbook_sha256": _sha256(workbook_backup),
        "backup_folder_inventory": _inventory(folder_backup),
    }
    (backup_dir / "manifest.json").write_bytes(_canonical_json(manifest) + b"\n")
    return backup_dir, workbook_backup, folder_backup


def _patch_package(source_workbook: Path, candidate: Path, row: dict[str, Any], link_info: dict[str, Any], new_target: str) -> dict[str, Any]:
    ET.register_namespace("", MAIN_NS)
    ET.register_namespace("r", DOC_REL_NS)
    ET.register_namespace("", PKG_REL_NS)
    with zipfile.ZipFile(source_workbook, "r") as zin:
        sheet_part, rel_part = _worksheet_parts(zin)
        if sheet_part != link_info["sheet_part"] or rel_part != link_info["rels_part"]:
            raise RepairRefused("Trade Log package relationships changed after planning")
        source_sheet_bytes = zin.read(sheet_part)
        source_rels_bytes = zin.read(rel_part)
        sheet_root = ET.fromstring(source_sheet_bytes)
        rels_root = ET.fromstring(source_rels_bytes)
        links = [h for h in sheet_root.findall(f"{_Q(MAIN_NS, 'hyperlinks')}/{_Q(MAIN_NS, 'hyperlink')}") if h.attrib.get("ref") == link_info["cell_ref"]]
        if len(links) != 1 or links[0].attrib.get(_Q(DOC_REL_NS, "id")) != link_info["relationship_id"]:
            raise RepairRefused("C377 hyperlink changed after the dry-run plan")
        target_link = links[0]
        old_rid = link_info["relationship_id"]
        old_rel = next((r for r in rels_root.findall(_Q(PKG_REL_NS, "Relationship")) if r.attrib.get("Id") == old_rid), None)
        if old_rel is None or old_rel.attrib.get("Target") != link_info["target"]:
            raise RepairRefused("C377 hyperlink relationship target changed after planning")
        if link_info["relationship_uses"] == 1:
            old_rel.attrib["Target"] = new_target
            new_rid = old_rid
        else:
            ids = {r.attrib.get("Id", "") for r in rels_root.findall(_Q(PKG_REL_NS, "Relationship"))}
            n = 1
            while f"rId{n}" in ids:
                n += 1
            new_rid = f"rId{n}"
            target_link.attrib[_Q(DOC_REL_NS, "id")] = new_rid
            ET.SubElement(rels_root, _Q(PKG_REL_NS, "Relationship"), {
                "Id": new_rid,
                "Type": old_rel.attrib["Type"],
                "Target": new_target,
                "TargetMode": "External",
            })
        patched_sheet = ET.tostring(sheet_root, encoding="utf-8", xml_declaration=True)
        ET.register_namespace("", PKG_REL_NS)
        patched_rels = ET.tostring(rels_root, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(candidate, "w") as zout:
            zout.comment = zin.comment
            for item in zin.infolist():
                data = patched_sheet if item.filename == sheet_part else patched_rels if item.filename == rel_part else zin.read(item.filename)
                zout.writestr(item, data)
    shutil.copystat(source_workbook, candidate)
    return {"sheet_part": sheet_part, "rels_part": rel_part, "old_relationship_id": old_rid,
            "new_relationship_id": new_rid, "relationship_was_shared": link_info["relationship_uses"] > 1}


def _xml_without_cell_hyperlink(data: bytes, cell_ref: str) -> bytes:
    root = ET.fromstring(data)
    parent = root.find(_Q(MAIN_NS, "hyperlinks"))
    if parent is not None:
        for link in list(parent):
            if link.attrib.get("ref") == cell_ref:
                parent.remove(link)
    ET.register_namespace("", MAIN_NS)
    ET.register_namespace("r", DOC_REL_NS)
    return ET.tostring(root, encoding="utf-8")


def _xml_without_relationships(data: bytes, ignored_ids: set[str]) -> bytes:
    root = ET.fromstring(data)
    for rel in list(root.findall(_Q(PKG_REL_NS, "Relationship"))):
        if rel.attrib.get("Id") in ignored_ids:
            root.remove(rel)
    ET.register_namespace("", PKG_REL_NS)
    return ET.tostring(root, encoding="utf-8")


def _verify_candidate(original: Path, candidate: Path, parts: dict[str, Any], cell_ref: str, link_info: dict[str, Any], new_target: str) -> None:
    with zipfile.ZipFile(original, "r") as old, zipfile.ZipFile(candidate, "r") as new:
        if new.testzip() is not None:
            raise RepairRefused("Candidate workbook ZIP integrity check failed")
        if set(old.namelist()) != set(new.namelist()):
            raise RepairRefused("Candidate workbook changed the package-part inventory")
        for name in old.namelist():
            if name not in {parts["sheet_part"], parts["rels_part"]} and old.read(name) != new.read(name):
                raise RepairRefused(f"Candidate changed unrelated workbook package part: {name}")
        if _xml_without_cell_hyperlink(old.read(parts["sheet_part"]), cell_ref) != _xml_without_cell_hyperlink(new.read(parts["sheet_part"]), cell_ref):
            raise RepairRefused("Candidate changed Trade Log cells or presentation outside C377's hyperlink")
        old_rid = link_info["relationship_id"]
        if parts["relationship_was_shared"]:
            old_ignore: set[str] = set()
            new_ignore = {parts["new_relationship_id"]}
        else:
            old_ignore = {old_rid}
            new_ignore = {parts["new_relationship_id"]}
        if _xml_without_relationships(old.read(parts["rels_part"]), old_ignore) != _xml_without_relationships(new.read(parts["rels_part"]), new_ignore):
            raise RepairRefused("Candidate changed worksheet relationships outside C377's existing hyperlink")
        sheet = ET.fromstring(new.read(parts["sheet_part"]))
        link_nodes = [h for h in sheet.findall(f"{_Q(MAIN_NS, 'hyperlinks')}/{_Q(MAIN_NS, 'hyperlink')}") if h.attrib.get("ref") == cell_ref]
        if len(link_nodes) != 1 or link_nodes[0].attrib.get(_Q(DOC_REL_NS, "id")) != parts["new_relationship_id"]:
            raise RepairRefused("Candidate C377 hyperlink reference was not installed exactly once")
        rels = ET.fromstring(new.read(parts["rels_part"]))
        rel = next((r for r in rels.findall(_Q(PKG_REL_NS, "Relationship")) if r.attrib.get("Id") == parts["new_relationship_id"]), None)
        if rel is None or rel.attrib.get("Target") != new_target or rel.attrib.get("TargetMode") != "External":
            raise RepairRefused("Candidate C377 hyperlink does not target OCTOBER/C377")


def _verify_saved_workbook(workbook: Path, new_folder: Path) -> dict[str, Any]:
    row = _workbook_row(workbook)
    info = _hyperlink_info(workbook, row)
    if not _same_path_uri(info["target"], new_folder):
        raise RepairRefused("Saved C377 hyperlink does not resolve to OCTOBER/C377")
    return {"row": row["row"], "trade_number": row["trade_number"], "row_id": row["row_id"],
            "account": row["account"], "symbol": row["symbol"], "open_time": row["open_time"].isoformat(),
            "close_time": row["close_time"].isoformat(), "hyperlink": info["target"]}


def _acquire_gate(path: Path, reserve: Callable[[Path], Callable[[], None]] | None) -> Callable[[], None]:
    return reserve(path) if reserve is not None else _acquire_existing_gate(path)


def repair(
    workbook_path: Path,
    json_path: Path,
    *,
    chart_root: Path | None = None,
    recovery_root: Path | None = None,
    apply: bool = False,
    confirm_plan_sha256: str | None = None,
    reserve: Callable[[Path], Callable[[], None]] | None = None,
) -> dict[str, Any]:
    """Return a verified plan by default; mutate only with the matching plan digest."""
    repo = Path(__file__).resolve().parents[1]
    recovery = recovery_root or (repo / ".job7f_recovery")
    plan = _build_plan(Path(workbook_path), Path(json_path), chart_root, Path(recovery))
    digest = plan_digest(plan)
    result = {"plan": plan, "plan_sha256": digest, "applied": False}
    if not apply:
        return result
    if confirm_plan_sha256 != digest:
        raise RepairRefused("Apply requires the SHA-256 from the reviewed dry-run plan")
    if plan["action"] == "verified_no_op":
        _TRADE_FOLDER_INDEX_CACHE.clear()
        return {**result, "verified_no_op": True, "verified_workbook": _verify_saved_workbook(Path(workbook_path), Path(plan["destination_folder"]))}

    workbook = Path(workbook_path).expanduser().resolve()
    root = Path(plan["chart_root"])
    source = Path(plan["source_folder"])
    destination = Path(plan["destination_folder"])
    month = destination.parent
    release = _acquire_gate(workbook, reserve)
    backup_dir: Path | None = None
    candidate: Path | None = None
    moved = False
    installed = False
    candidate_hash = ""
    created_month = False
    rollback: dict[str, Any] = {"workbook_restored": False, "folder_restored": False, "complete": True}
    try:
        _assert_workbook_available(workbook)
        current = _build_plan(workbook, Path(plan["json_path"]), chart_root, Path(plan["recovery_root"]))
        if plan_digest(current) != digest:
            raise RepairRefused("C377, its link, chart files or workbook changed after the dry-run plan")
        backup_dir, workbook_backup, folder_backup = _copy_backup(plan, Path(plan["recovery_root"]))
        link_info = _hyperlink_info(workbook, _workbook_row(workbook))
        candidate = workbook.with_name(f".{workbook.stem}.C377-{uuid.uuid4().hex}.candidate{workbook.suffix}")
        parts = _patch_package(workbook, candidate, _workbook_row(workbook), link_info, plan["new_link"])
        _verify_candidate(workbook, candidate, parts, link_info["cell_ref"], link_info, plan["new_link"])
        candidate_hash = _sha256(candidate)
        if not month.exists():
            month.mkdir()
            created_month = True
        if destination.exists() or not source.is_dir():
            raise RepairRefused("C377 source/destination changed before the atomic folder move")
        if _inventory(source) != plan["inventory"]:
            raise RepairRefused("C377 contents changed after backup; refusing to move them")
        source.rename(destination)
        moved = True
        if _inventory(destination) != plan["inventory"]:
            raise RepairRefused("Moved C377 inventory differs from the verified backup")
        os.replace(candidate, workbook)
        installed = True
        verified = _verify_saved_workbook(workbook, destination)
        if _sha256(workbook) != candidate_hash or _inventory(destination) != plan["inventory"]:
            raise RepairRefused("Final workbook bytes or moved chart files changed during verification")
        april = source.parent
        april_removed = False
        try:
            april.rmdir()
            april_removed = True
        except OSError:
            pass
        _TRADE_FOLDER_INDEX_CACHE.clear()
        return {**result, "applied": True, "verified_no_op": False, "backup_dir": str(backup_dir),
                "workbook_backup": str(workbook_backup), "chart_folder_backup": str(folder_backup),
                "verified_workbook": verified, "inventory": plan["inventory"], "april_parent_removed_if_empty": april_removed,
                "workbook_sha256": _sha256(workbook), "candidate_package_parts": parts}
    except Exception as exc:
        rollback_errors = []
        if installed and backup_dir is not None:
            if workbook.exists() and _sha256(workbook) == candidate_hash:
                restore_candidate = workbook.with_name(f".{workbook.name}.C377-restore-{uuid.uuid4().hex}.tmp")
                shutil.copy2(backup_dir / "Trading Journal.xlsx", restore_candidate)
                os.replace(restore_candidate, workbook)
                rollback["workbook_restored"] = _sha256(workbook) == plan["workbook_sha256"]
            else:
                rollback_errors.append("workbook changed after candidate install; backup was not written over it")
        if moved:
            if destination.is_dir() and not source.exists() and _inventory(destination) == plan["inventory"]:
                source.parent.mkdir(parents=True, exist_ok=True)
                destination.rename(source)
                rollback["folder_restored"] = _inventory(source) == plan["inventory"]
            else:
                rollback_errors.append("moved folder changed or source reappeared; concurrent contents were preserved")
        if created_month:
            try:
                month.rmdir()
            except OSError:
                pass
        rollback["complete"] = not rollback_errors and (not installed or rollback["workbook_restored"]) and (not moved or rollback["folder_restored"])
        rollback["errors"] = rollback_errors
        raise RepairRefused(f"C377 repair failed: {exc}; rollback={json.dumps(rollback, sort_keys=True)}; backup={backup_dir}") from exc
    finally:
        if candidate is not None and candidate.exists():
            candidate.unlink()
        release()



@__import__('contextlib').contextmanager
def _number_repair_gate(workbook: Path, *, lock_path: Path | None = None):
    """Reuse the actual journal OS gate without importing/starting the service."""
    import ast, time
    source = (_REPO_ROOT/'render/master_service.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    names={'_journal_transaction_lock_path','_try_acquire_journal_os_lock','_release_journal_os_lock'}
    body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
    if len(body)!=3: raise RepairRefused('Canonical journal gate definitions changed')
    namespace=dict(Path=Path,os=os,errno=errno)
    exec(compile(ast.Module(body=body,type_ignores=[]),str(_REPO_ROOT/'render/master_service.py'),'exec'),namespace)
    if lock_path is not None: namespace['_journal_transaction_lock_path']=lambda path:lock_path
    deadline=time.monotonic()+5;handle=None
    while handle is None:
        handle=namespace['_try_acquire_journal_os_lock'](workbook)
        if handle is None:
            if time.monotonic()>=deadline:raise RepairRefused('Journal writer gate is busy')
            time.sleep(.05)
    try:yield
    finally:namespace['_release_journal_os_lock'](handle)


def _number_inventory(folder: Path):
    """Bounded non-reparse content manifest; streamed hashes, no folder copies."""
    import stat,time
    started=time.monotonic();result=[];stack=[folder]
    for parent in [folder,*folder.parents]:
        if parent.lstat().st_file_attributes & 1024:raise RepairRefused('Reparse ancestry')
    while stack:
        parent=stack.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                if time.monotonic()-started>60 or len(result)>=10000:raise RepairRefused('Folder inventory bound exceeded')
                path=Path(entry.path);st=path.lstat()
                if st.st_file_attributes & 1024:raise RepairRefused('Reparse chart content')
                if stat.S_ISDIR(st.st_mode):typ='directory';digest=None;stack.append(path)
                elif stat.S_ISREG(st.st_mode):typ='file';digest=_sha256(path)
                else:raise RepairRefused('Unsupported chart content')
                result.append(dict(relative=path.relative_to(folder).as_posix(),type=typ,size=st.st_size if typ=='file' else 0,sha256=digest))
    return sorted(result,key=lambda entry:entry['relative'])


def _number_atomic_json(path: Path, value):
    temporary=path.with_name('.'+path.name+'.number-repair-'+uuid.uuid4().hex+'.tmp')
    owned=False
    try:
        with temporary.open('x',encoding='utf-8') as handle:
            owned=True
            json.dump(value,handle,ensure_ascii=False,indent=2);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:
        if owned and temporary.exists():temporary.unlink()


def _number_pointer(document,pointer):
    target=document
    for key in pointer[:-1]:
        if isinstance(key,dict):
            matches=[item for item in target if isinstance(item,dict) and all(item.get(k)==v for k,v in key.items())]
            if len(matches)!=1:raise RepairRefused('Sparse record selector is not unique')
            target=matches[0]
        else:target=target[key]
    return target,pointer[-1]


def _validate_test_fields(before,fields,authorization,*,row_id=None,context_order=None):
    """Permit only the declared BTC manual-Test overlay; preserve all other metadata."""
    if not authorization or authorization.get('value') is not True:raise RepairRefused('Explicit single-row Test authorization required')
    is_row=row_id==authorization['id'];is_context=context_order==authorization['order_id']
    if not is_row and not is_context:raise RepairRefused('Test edit targets another execution/context')
    for key,value in fields.items():
        if key=='is_test_trade':
            if not is_row or value is not True:raise RepairRefused('Only authorized row Test=True is permitted')
        elif key in ('manual_overrides','metrics','classification_overrides'):
            if key=='classification_overrides' and not is_context:raise RepairRefused('Wrong classification target')
            if key!='classification_overrides' and not is_row:raise RepairRefused('Wrong manual override target')
            expected=dict(before.get(key) or {});expected['is_test_trade']=True
            if value!=expected:raise RepairRefused('Unrelated manual/metric classification change')
        elif key=='manual_override_fields':
            if not is_row or value!=sorted(set(before.get(key) or [])|{'is_test_trade'}):raise RepairRefused('Unrelated manual override fields changed')
        elif key=='classification_version':
            if not is_context or value!=int(before.get(key) or 0)+1:raise RepairRefused('Classification version must advance once')
        elif key in ('manual_updated_at','classification_updated_at'):
            if (key=='manual_updated_at' and not is_row) or (key=='classification_updated_at' and not is_context):raise RepairRefused('Wrong classification timestamp target')
            datetime.fromisoformat(value)
        else:raise RepairRefused('Non-Test field in classification change')


def apply_number_repair_plan(plan, *, gate=None, failpoint=None):
    """One explicit, hash-bound transaction; sparse field undo and folder renames.

    All broker/financial/Test fields are excluded from this maintenance operation.
    The caller supplies the exact captured plan; no symbol/number discovery runs.
    """
    from contextlib import ExitStack
    import sqlite3,time
    from tools import master_journal_workbook as m
    started=time.monotonic()
    if not __debug__:raise RepairRefused('Preservation verification requires normal Python assertions')
    if plan.get('conflicts'):raise RepairRefused('Unresolved allocation/folder conflicts')
    workbook=Path(plan['workbook_path']);candidate=Path(plan['candidate_path']);recovery=Path(plan['recovery_root'])
    root=Path(plan['chart_root']);month=root/'2026'/'OCTOBER'
    for target in [workbook,candidate,Path(plan['sqlite_path']),*[Path(store['path']) for store in plan['json_stores']]]:
        for ancestor in [target,*target.parents]:
            if ancestor.exists() and ancestor.lstat().st_file_attributes & 1024:raise RepairRefused('Reparse storage path')
    for parent in [month,*month.parents]:
        if parent.lstat().st_file_attributes & 1024:raise RepairRefused('Reparse root')
    folder_ops=plan['folder_moves'];scaffolds=plan.get('obsolete_scaffolds',[]);creates=plan.get('folder_creates',[])
    for path in [Path(e[k]) for e in folder_ops for k in ('source','destination')]+[Path(e['path']) for e in scaffolds+creates]:
        if path.parent!=month or path.name.startswith('.'):raise RepairRefused('Folder plan escapes exact October scope')
    allowed={'trade_number','trade_folder','folder','identities','status','retired_from','retired_reason','retired_number_reservation'}
    test_fields={'is_test_trade','manual_overrides','manual_override_fields','manual_updated_at','metrics','classification_overrides','classification_version','classification_updated_at'}
    authorization=plan.get('test_classification')
    for coord,edit in plan.get('test_cell_updates',{}).items():
        if not authorization or edit['id']!=authorization['id'] or coord!=authorization['test_cell'] or edit['new_value']!='Yes':raise RepairRefused('Unauthorized Test cell edit')
    for store in plan['json_stores']:
        for change in store['changes']:
            if change['pointer'][-1] not in allowed|test_fields:raise RepairRefused('Non-number/Test JSON field in plan')
            if change['pointer'][-1] in test_fields and not authorization:raise RepairRefused('Missing Test authorization')
            if change['pointer'][-1]=='folder' and Path(store['path']).name!='trade_folder_allocations.json':raise RepairRefused('Folder field restricted to allocation registry')
    for record in plan['sql_records']:
        if record.get('table','journal_trades') not in {'journal_trades','journal_metrics'}:raise RepairRefused('Unexpected SQL table')
        fields={k:v for k,v in record['fields'].items() if k not in {'trade_number','trade_folder'}}
        if fields:_validate_test_fields(json.loads(record['before']),fields,authorization,row_id=record['id'])
        if record.get('table')=='journal_metrics' and (not authorization or record['id']!=authorization['id'] or set(record['fields'])!={'is_test_trade'}):raise RepairRefused('Only this BTC Test metric may change')
    ledger_path=recovery/'transaction.json';claim=recovery/'apply.claim'
    if ledger_path.exists():
        saved=json.loads(ledger_path.read_text())
        if saved.get('phase')=='committed':
            if _sha256(workbook)!=plan['candidate_sha256']:raise RepairRefused('Committed workbook changed; not an idempotent replay')
            for move in folder_ops:
                if _number_inventory(Path(move['destination']))!=move['inventory']:raise RepairRefused('Committed folder changed')
            for store in plan['json_stores']:
                document=json.loads(Path(store['path']).read_text(encoding='utf-8'))
                for change in store['changes']:
                    target,key=_number_pointer(document,change['pointer'])
                    if target.get(key)!=change['after']:raise RepairRefused('Committed store changed')
            return dict(ok=True,noop=True,phase='committed')
        raise RepairRefused('An earlier/incomplete attempt exists; new authorization required')
    for ancestor in [recovery,*recovery.parents]:
        if ancestor.exists() and ancestor.lstat().st_file_attributes & 1024:raise RepairRefused('Reparse recovery path')
    recovery.mkdir(parents=True,exist_ok=True)
    if claim.exists():raise RepairRefused('Apply attempt already claimed; no retry')
    moved=[];created=[];removed=[];stores_written=[];installed=False;sql_committed=False;con=None
    ledger=dict(phase='preflight',steps=[],plan_sha256=plan_digest(plan))
    def event(kind,**fields):
        ledger['steps'].append(dict(kind=kind,**fields));_number_atomic_json(ledger_path,ledger)
        if failpoint is not None:failpoint(kind)
        if time.monotonic()-started>280:raise TimeoutError('Number repair transaction deadline')
    with ExitStack() as resources:
        resources.enter_context(gate() if gate is not None else _number_repair_gate(workbook))
        resources.enter_context(m._trade_allocation_lock(timeout_seconds=5))
        _assert_workbook_available(workbook)
        if _sha256(workbook)!=plan['workbook_sha256'] or _sha256(candidate)!=plan['candidate_sha256']:raise RepairRefused('Workbook/candidate revision changed')
        m.audit_existing_trade_number_package(workbook.read_bytes(),candidate.read_bytes(),plan['package_updates'],test_updates=plan.get('test_cell_updates'))
        documents=[]
        for store in plan['json_stores']:
            path=Path(store['path'])
            if _sha256(path)!=store['sha256']:raise RepairRefused('Canonical JSON revision changed: '+str(path))
            document=json.loads(path.read_text(encoding='utf-8'))
            for change in store['changes']:
                target,key=_number_pointer(document,change['pointer'])
                if (key in target)!=change['before_exists'] or target.get(key)!=change['before']:raise RepairRefused('JSON before-image changed')
                if key in test_fields:
                    _validate_test_fields(target,{key:change['after']},authorization,row_id=target.get('id'),context_order=target.get('order_id'))
                target[key]=change['after']
            documents.append((store,document))
        sources={str(Path(e['source'])) for e in folder_ops}
        disposable={str(Path(e['path'])) for e in scaffolds}
        for move in folder_ops:
            if _number_inventory(Path(move['source']))!=move['inventory']:raise RepairRefused('Chart contents changed')
            destination=Path(move['destination'])
            if destination.exists() and str(destination) not in sources|disposable:raise RepairRefused('Unrelated destination collision')
        for scaffold in scaffolds:
            if _number_inventory(Path(scaffold['path']))!=scaffold['inventory']:raise RepairRefused('Obsolete scaffold changed')
            if [e['relative'] for e in scaffold['inventory']]!=['.ltt-trade-owner.json']:raise RepairRefused('Scaffold contains user content')
        for item in creates:
            if Path(item['path']).exists() and str(Path(item['path'])) not in sources|disposable:raise RepairRefused('Create destination is occupied')
        con=sqlite3.connect(plan['sqlite_path'],timeout=5)
        resources.callback(con.close)
        con.execute('BEGIN IMMEDIATE')
        resources.callback(con.rollback)
        for record in plan['sql_records']:
            old=con.execute('SELECT payload_json,imported_at FROM '+record.get('table','journal_trades')+' WHERE id=?',(record['id'],)).fetchone()
            if old!=(record['before'],record['imported_at']):raise RepairRefused('SQL before-image changed')
        recovery_bytes=workbook.stat().st_size+len(json.dumps(plan).encode())
        peak=recovery_bytes+candidate.stat().st_size+max([len(json.dumps(document).encode()) for _,document in documents]+[0])
        if recovery_bytes>25*1024**2 or peak>50*1024**2:raise RepairRefused('Recovery/candidate ceiling exceeded')
        with claim.open('x') as handle:handle.write(plan_digest(plan));handle.flush();os.fsync(handle.fileno())
        # One original workbook, no SQL or whole-cache backup. Field undo stays sparse.
        backup=Path(plan.get('original_backup_path') or recovery/'original.xlsx')
        if backup.exists():
            if _sha256(backup)!=plan['workbook_sha256']:raise RepairRefused('Original recovery conflict')
        else:
            with backup.open('xb') as handle:handle.write(workbook.read_bytes())
        ledger.update(phase='applying',recovery_bytes=recovery_bytes,peak_bytes=peak,json_undo=plan['json_stores'],sql_undo=plan['sql_records'])
        _number_atomic_json(ledger_path,ledger)
        try:
            for item in scaffolds:
                path=Path(item['path']);marker=path/'.ltt-trade-owner.json';raw=marker.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=item['inventory'][0]['sha256']:raise RepairRefused('Scaffold marker changed')
                removed.append((path,raw));marker.unlink();path.rmdir();event('scaffold_removed',path=str(path))
            for index,move in enumerate(folder_ops):
                source=Path(move['source']);temporary=month/('.ltt-number-'+plan_digest(plan)[:12]+'-'+str(index))
                if temporary.exists():raise RepairRefused('Temporary sibling collision')
                source.rename(temporary);moved.append(dict(source=source,current=temporary,destination=Path(move['destination']),inventory=move['inventory']));event('folder_staged',source=str(source),temporary=str(temporary))
            for item in moved:
                if item['destination'].exists():raise RepairRefused('Final folder appeared')
                item['current'].rename(item['destination']);item['current']=item['destination'];event('folder_installed',destination=str(item['destination']))
            for item in creates:
                path=Path(item['path']);path.mkdir();created.append(path)
                with (path/'.ltt-trade-owner.json').open('x',encoding='utf-8') as handle:json.dump(item['owner'],handle);handle.flush();os.fsync(handle.fileno())
                event('folder_created',path=str(path))
            for store,document in documents:
                path=Path(store['path'])
                if _sha256(path)!=store['sha256']:raise RepairRefused('JSON changed before replacement')
                _number_atomic_json(path,document);stores_written.append((store,_sha256(path)));event('json_installed',path=str(path))
            for record in plan['sql_records']:
                value=json.loads(record['before']);value.update(record['fields']);payload=json.dumps(value,ensure_ascii=False)
                con.execute('UPDATE '+record.get('table','journal_trades')+' SET payload_json=? WHERE id=? AND payload_json=?',(payload,record['id'],record['before']))
                if con.execute('SELECT changes()').fetchone()[0]!=1:raise RepairRefused('SQL ownership changed')
            _assert_workbook_available(workbook)
            if _sha256(workbook)!=plan['workbook_sha256']:raise RepairRefused('Workbook changed before replacement')
            temporary=workbook.with_name('.'+workbook.name+'.number-candidate')
            if temporary.exists():raise RepairRefused('Workbook temporary candidate exists')
            with temporary.open('xb') as handle:handle.write(candidate.read_bytes());handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,workbook);installed=True;event('workbook_installed')
            if _sha256(workbook)!=plan['candidate_sha256']:raise RepairRefused('Installed workbook hash mismatch')
            m.audit_existing_trade_number_package(backup.read_bytes(),workbook.read_bytes(),plan['package_updates'],test_updates=plan.get('test_cell_updates'))
            for item in moved:
                if _number_inventory(item['destination'])!=item['inventory']:raise RepairRefused('Moved content mismatch')
            for store,_ in stores_written:
                document=json.loads(Path(store['path']).read_text(encoding='utf-8'))
                for change in store['changes']:
                    target,key=_number_pointer(document,change['pointer'])
                    if target.get(key)!=change['after']:raise RepairRefused('Installed JSON mismatch')
            for record in plan['sql_records']:
                current=con.execute('SELECT payload_json FROM '+record.get('table','journal_trades')+' WHERE id=?',(record['id'],)).fetchone()
                expected=json.loads(record['before']);expected.update(record['fields'])
                if not current or json.loads(current[0])!=expected:raise RepairRefused('SQL installed readback mismatch')
            for path in plan.get('absent_after',[]):
                if Path(path).exists():raise RepairRefused('Obsolete affected folder remains: '+path)
            con.commit();sql_committed=True;event('sql_committed')
            ledger['phase']='committed';_number_atomic_json(ledger_path,ledger)
            for cache_key in list(m._TRADE_FOLDER_INDEX_CACHE):
                if str(cache_key[0]).casefold()==str(root).casefold() and cache_key[1]=='C':
                    m._TRADE_FOLDER_INDEX_CACHE.pop(cache_key,None)
            return dict(ok=True,noop=False,phase='committed',folders_moved=len(moved),scaffolds_removed=len(removed),folders_created=len(created),sql_records=len(plan['sql_records']),workbook_cells=len(plan['package_updates']),elapsed=time.monotonic()-started)
        except BaseException as error:
            rollback_errors=[]
            try:
                if not sql_committed:con.rollback()
                else:
                    con.execute('BEGIN IMMEDIATE')
                    for record in plan['sql_records']:
                        expected=json.loads(record['before']);expected.update(record['fields']);current=con.execute('SELECT payload_json FROM '+record.get('table','journal_trades')+' WHERE id=?',(record['id'],)).fetchone()
                        if not current or json.loads(current[0])!=expected:raise RepairRefused('SQL rollback ownership lost')
                        con.execute('UPDATE '+record.get('table','journal_trades')+' SET payload_json=? WHERE id=?',(record['before'],record['id']))
                    con.commit()
                if installed:
                    if _sha256(workbook)!=plan['candidate_sha256']:raise RepairRefused('Workbook rollback ownership lost')
                    temporary=workbook.with_name('.'+workbook.name+'.number-undo')
                    with temporary.open('xb') as handle:handle.write(backup.read_bytes());handle.flush();os.fsync(handle.fileno())
                    os.replace(temporary,workbook)
                for store,owned in reversed(stores_written):
                    path=Path(store['path'])
                    if _sha256(path)!=owned:raise RepairRefused('JSON rollback ownership lost')
                    document=json.loads(path.read_text(encoding='utf-8'))
                    for change in store['changes']:
                        target,key=_number_pointer(document,change['pointer'])
                        if target.get(key)!=change['after']:raise RepairRefused('Sparse JSON rollback ownership lost')
                        if change['before_exists']:target[key]=change['before']
                        else:target.pop(key,None)
                    _number_atomic_json(path,document)
                for path in reversed(created):
                    marker=path/'.ltt-trade-owner.json'
                    if sorted(x.name for x in path.iterdir())!=['.ltt-trade-owner.json']:raise RepairRefused('Created folder gained content; preserve it')
                    marker.unlink();path.rmdir()
                # Stage all moved directories again before reversing a rename cycle.
                for i,item in enumerate(moved):
                    if _number_inventory(item['current'])!=item['inventory']:raise RepairRefused('Folder rollback ownership lost')
                    undo=month/('.ltt-number-undo-'+plan_digest(plan)[:12]+'-'+str(i))
                    if undo.exists():raise RepairRefused('Rollback temporary collision')
                    item['current'].rename(undo);item['current']=undo
                for item in moved:
                    if item['source'].exists():raise RepairRefused('Rollback source occupied')
                    item['current'].rename(item['source'])
                for path,raw in removed:
                    path.mkdir()
                    with (path/'.ltt-trade-owner.json').open('xb') as handle:handle.write(raw)
            except BaseException as rollback_error:rollback_errors.append(str(rollback_error))
            ledger.update(phase='rolled_back' if not rollback_errors else 'rollback_incomplete',error=str(error),rollback_errors=rollback_errors)
            _number_atomic_json(ledger_path,ledger)
            raise RepairRefused(json.dumps(dict(error=str(error),rollback_complete=not rollback_errors,rollback_errors=rollback_errors))) from error


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=repo / "journal" / "Trading Journal.xlsx")
    parser.add_argument("--json", type=Path, default=repo / "render" / "data" / "trading_journal.json")
    parser.add_argument("--chart-root", type=Path, default=None)
    parser.add_argument("--recovery-root", type=Path, default=repo / ".job7f_recovery")
    parser.add_argument("--apply", action="store_true", help="apply only the exact C377 mapping in a reviewed plan")
    parser.add_argument("--confirm-plan-sha256", default="")
    parser.add_argument('--number-plan',type=Path,default=None)
    parser.add_argument('--confirm-number-plan-sha256',default='')
    args = parser.parse_args(argv)
    if args.number_plan is not None:
        try:
            plan=json.loads(args.number_plan.read_text(encoding='utf-8'))
            if not args.apply or plan_digest(plan)!=args.confirm_number_plan_sha256:
                raise RepairRefused('Explicit apply and exact reviewed number-plan digest required')
            result=apply_number_repair_plan(plan)
            print(json.dumps(result,indent=2));return 0
        except Exception as error:
            print(json.dumps(dict(ok=False,error=str(error)),indent=2));return 2
    try:
        result = repair(args.workbook, args.json, chart_root=args.chart_root, recovery_root=args.recovery_root,
                        apply=args.apply, confirm_plan_sha256=args.confirm_plan_sha256 or None)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
