"""Bounded, read-only XLSX statement import using the OpenXML package format."""
from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from fastapi import HTTPException

from . import statements

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_ARCHIVE_ENTRIES = 2_000
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_XML_BYTES = 12 * 1024 * 1024
MAX_SHARED_STRINGS = 100_000
MAX_ROWS = 5_010
MAX_COLUMNS = 32
MAX_CELL_CHARS = 2_000
DATE_FORMAT_IDS = set(range(14, 23)) | {27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 50, 51, 52, 53, 54, 55, 56, 57, 58, 45, 46, 47}


class SpreadsheetImportError(ValueError):
    """The workbook is unsafe, malformed, or does not contain a supported statement."""


def _read_member(archive: zipfile.ZipFile, name: str, limit: int = MAX_XML_BYTES) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise SpreadsheetImportError("The XLSX package is missing a required workbook part.") from exc
    if info.file_size > limit:
        raise SpreadsheetImportError("The XLSX workbook part exceeds its safe size limit.")
    with archive.open(info) as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise SpreadsheetImportError("The XLSX workbook part exceeds its safe size limit.")
    return content


def _xml(archive: zipfile.ZipFile, name: str, limit: int = MAX_XML_BYTES):
    try:
        return ET.fromstring(_read_member(archive, name, limit))
    except ET.ParseError as exc:
        raise SpreadsheetImportError("The XLSX workbook contains malformed XML.") from exc


def _column_index(reference: str) -> int:
    match = re.fullmatch(r"([A-Z]{1,3})\d+", reference.upper())
    if not match:
        raise SpreadsheetImportError("The XLSX sheet contains an invalid cell reference.")
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - 64
    if value > MAX_COLUMNS:
        raise SpreadsheetImportError(f"XLSX statements are limited to {MAX_COLUMNS} columns.")
    return value - 1


def _date_format(styles_root) -> set[int]:
    formats = {}
    num_formats = styles_root.find(f"{{{MAIN_NS}}}numFmts")
    if num_formats is not None:
        for entry in num_formats.findall(f"{{{MAIN_NS}}}numFmt"):
            try:
                formats[int(entry.attrib.get("numFmtId", "-1"))] = entry.attrib.get("formatCode", "")
            except ValueError:
                continue
    date_styles = set()
    xfs = styles_root.find(f"{{{MAIN_NS}}}cellXfs")
    if xfs is not None:
        for index, xf in enumerate(xfs):
            try:
                fmt_id = int(xf.attrib.get("numFmtId", "0"))
            except ValueError:
                continue
            code = re.sub(r'"[^"]*"|\\.|_.|\*.', "", formats.get(fmt_id, "")).casefold()
            if fmt_id in DATE_FORMAT_IDS or re.search(r"[yd]", code) and "m" in code:
                date_styles.add(index)
    return date_styles


def _excel_value(cell, shared_strings: list[str], date_styles: set[int], date_1904: bool) -> str:
    kind = cell.attrib.get("t", "n")
    if kind == "inlineStr":
        inline = cell.find(f"{{{MAIN_NS}}}is")
        value = "" if inline is None else "".join(text.text or "" for text in inline.iter(f"{{{MAIN_NS}}}t"))
    else:
        node = cell.find(f"{{{MAIN_NS}}}v")
        raw = "" if node is None or node.text is None else node.text
        if kind == "s":
            try:
                value = shared_strings[int(raw)]
            except (ValueError, IndexError) as exc:
                raise SpreadsheetImportError("The XLSX workbook references an invalid shared string.") from exc
        elif kind == "b":
            value = "TRUE" if raw == "1" else "FALSE" if raw == "0" else ""
        elif kind in {"str", "e"}:
            value = raw
        elif kind == "n" or not kind:
            style = cell.attrib.get("s", "0")
            try:
                style_index = int(style)
            except ValueError:
                style_index = 0
            if raw and style_index in date_styles:
                try:
                    days = Decimal(raw)
                    epoch = datetime(1904, 1, 1) if date_1904 else datetime(1899, 12, 30)
                    converted = epoch + timedelta(days=float(days))
                    value = converted.isoformat(sep=" ") if converted.time().isoformat() != "00:00:00" else converted.date().isoformat()
                except (InvalidOperation, OverflowError, ValueError) as exc:
                    raise SpreadsheetImportError("An XLSX date cell contains an invalid serial value.") from exc
            elif raw:
                try:
                    value = format(Decimal(raw), "f")
                except InvalidOperation as exc:
                    raise SpreadsheetImportError("An XLSX numeric cell contains an invalid value.") from exc
            else:
                value = ""
        else:
            raise SpreadsheetImportError("The XLSX workbook contains an unsupported cell value type.")
    value = str(value)
    if len(value) > MAX_CELL_CHARS:
        raise SpreadsheetImportError("An XLSX cell exceeds the safe text length limit.")
    return value


def _sheet_parts(archive: zipfile.ZipFile):
    root_relations = _xml(archive, "_rels/.rels")
    office_document = next((relation for relation in root_relations.findall(f"{{{PKG_REL_NS}}}Relationship")
                            if relation.attrib.get("Type", "").endswith("/officeDocument")
                            and relation.attrib.get("TargetMode") != "External"), None)
    if office_document is None or posixpath.normpath(office_document.attrib.get("Target", "")) != "xl/workbook.xml":
        raise SpreadsheetImportError("The XLSX package does not reference a supported workbook part.")
    content_types = _xml(archive, "[Content_Types].xml")
    declared_parts = {node.attrib.get("PartName", "").lstrip("/") for node in content_types}
    if "xl/workbook.xml" not in declared_parts or not any(
            name.startswith("xl/worksheets/") for name in declared_parts):
        raise SpreadsheetImportError("The XLSX package content types are incomplete.")
    workbook = _xml(archive, "xl/workbook.xml")
    relationships = _xml(archive, "xl/_rels/workbook.xml.rels")
    targets = {}
    for relation in relationships.findall(f"{{{PKG_REL_NS}}}Relationship"):
        if relation.attrib.get("TargetMode") == "External":
            continue
        relation_id = relation.attrib.get("Id")
        target = relation.attrib.get("Target", "")
        if not relation_id or not target:
            continue
        if target.startswith("/"):
            path = posixpath.normpath(target.lstrip("/"))
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        if path.startswith("xl/worksheets/") and ".." not in PurePosixPath(path).parts:
            targets[relation_id] = path
    sheets = workbook.find(f"{{{MAIN_NS}}}sheets")
    if sheets is None:
        raise SpreadsheetImportError("The XLSX workbook contains no worksheets.")
    result = []
    for sheet in sheets:
        name = sheet.attrib.get("name", "Sheet")[:100]
        relation_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id")
        if relation_id in targets:
            result.append((name, targets[relation_id]))
    if not result:
        raise SpreadsheetImportError("The XLSX workbook contains no readable worksheets.")
    return workbook, result


def _sheet_rows(archive, path, shared_strings, date_styles, date_1904):
    root = _xml(archive, path)
    data = root.find(f"{{{MAIN_NS}}}sheetData")
    if data is None:
        return []
    rows = []
    for row in data.findall(f"{{{MAIN_NS}}}row"):
        row_number = row.attrib.get("r", "")
        try:
            number = int(row_number)
        except ValueError as exc:
            raise SpreadsheetImportError("The XLSX sheet contains an invalid row number.") from exc
        if not 1 <= number <= MAX_ROWS:
            raise SpreadsheetImportError(f"XLSX statements are limited to {MAX_ROWS} rows.")
        cells = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            col = _column_index(cell.attrib.get("r", ""))
            cells[col] = _excel_value(cell, shared_strings, date_styles, date_1904)
        rows.append((number, cells))
    return rows


def parse_statement_xlsx(data: bytes) -> dict:
    """Read the first sheet with an unambiguous statement header; formulas are never evaluated."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES or sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
                raise SpreadsheetImportError("The XLSX package exceeds its safe expanded size limits.")
            if len({item.filename for item in infos}) != len(infos) or any(item.flag_bits & 0x1 for item in infos):
                raise SpreadsheetImportError("The XLSX package contains duplicate or encrypted parts.")
            if sum(name.startswith("xl/worksheets/") and name.endswith(".xml") for name in (item.filename for item in infos)) > 50:
                raise SpreadsheetImportError("The XLSX workbook has too many worksheets to inspect safely.")
            names = {item.filename for item in infos}
            if "xl/vbaProject.bin" in names or any(name.startswith("xl/externalLinks/") for name in names):
                raise SpreadsheetImportError("Macro-enabled workbooks and external workbook links are not supported.")
            workbook, sheets = _sheet_parts(archive)
            workbook_properties = workbook.find(f"{{{MAIN_NS}}}workbookPr")
            date_1904 = workbook_properties is not None and workbook_properties.attrib.get("date1904", "0").casefold() in {"1", "true"}
            shared_strings = []
            if "xl/sharedStrings.xml" in names:
                shared_root = _xml(archive, "xl/sharedStrings.xml", 8 * 1024 * 1024)
                shared_node = shared_root.findall(f"{{{MAIN_NS}}}si")
                if len(shared_node) > MAX_SHARED_STRINGS:
                    raise SpreadsheetImportError("The XLSX workbook has too many shared strings.")
                shared_strings = ["".join(text.text or "" for text in item.iter(f"{{{MAIN_NS}}}t")) for item in shared_node]
            date_styles = set()
            if "xl/styles.xml" in names:
                date_styles = _date_format(_xml(archive, "xl/styles.xml", 2 * 1024 * 1024))
            selected = None
            for sheet_name, sheet_path in sheets:
                rows = _sheet_rows(archive, sheet_path, shared_strings, date_styles, date_1904)
                for index, (_number, cells) in enumerate(rows[:30]):
                    if not cells:
                        continue
                    max_col = max(cells) + 1
                    values = [cells.get(col, "") for col in range(max_col)]
                    if statements.is_statement_header(values):
                        selected = (sheet_name, rows, index, max_col)
                        break
                if selected:
                    break
            if selected is None:
                raise SpreadsheetImportError("No worksheet has a recognizable bank-statement transaction header.")
            sheet_name, rows, header_index, max_col = selected
            header_row_number, header_cells = rows[header_index]
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow([header_cells.get(col, "") for col in range(max_col)])
            row_numbers = []
            for excel_row, cells in rows[header_index + 1:]:
                writer.writerow([cells.get(col, "") for col in range(max_col)])
                row_numbers.append(excel_row)
            parsed = statements.parse_statement_csv(output.getvalue().encode("utf-8"))
            for row in parsed["transactions"]:
                index = row["row_number"] - 2
                excel_row = row_numbers[index] if 0 <= index < len(row_numbers) else header_row_number + row["row_number"] - 1
                row["row_number"] = excel_row
                row["source_sheet"] = sheet_name
            for candidate in parsed["statement_analysis"].get("duplicate_candidates", []):
                candidate["rows"] = [row_numbers[number - 2] for number in candidate["rows"] if 2 <= number <= len(row_numbers) + 1]
                candidate["source_sheet"] = sheet_name
            for issue in parsed["statement_analysis"].get("balance_issues", []):
                index = issue["row_number"] - 2
                if 0 <= index < len(row_numbers):
                    issue["row_number"] = row_numbers[index]
                issue["source_sheet"] = sheet_name
            parsed["statement_analysis"]["source_format"] = "xlsx"
            parsed["statement_analysis"]["source_sheet"] = sheet_name
            return parsed
    except zipfile.BadZipFile as exc:
        raise SpreadsheetImportError("The XLSX file is not a valid OpenXML workbook.") from exc
