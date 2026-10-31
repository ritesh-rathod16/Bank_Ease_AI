"""Bounded text extraction from Word OpenXML files without executing embedded content."""
from __future__ import annotations

from io import BytesIO
import posixpath
import zipfile
from xml.etree import ElementTree as ET


MAX_ARCHIVE_ENTRIES = 2_000
MAX_UNCOMPRESSED_BYTES = 24 * 1024 * 1024
MAX_XML_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARACTERS = 500_000
MAX_SECTIONS = 100
MAX_SECTION_CHARACTERS = 4_000
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class DocxImportError(ValueError):
    """The file is not a safe, readable DOCX text document."""


def _read(archive: zipfile.ZipFile, name: str, limit: int = MAX_XML_BYTES) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise DocxImportError("The DOCX package is missing a required document part.") from exc
    if info.file_size > limit:
        raise DocxImportError("A DOCX document part exceeds its safe size limit.")
    with archive.open(info) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise DocxImportError("A DOCX document part exceeds its safe size limit.")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise DocxImportError("DOCX XML declarations with document entities are not accepted.")
    return data


def _parse(archive: zipfile.ZipFile, name: str):
    try:
        return ET.fromstring(_read(archive, name))
    except ET.ParseError as exc:
        raise DocxImportError("The DOCX package contains malformed XML.") from exc


def _paragraph_text(paragraph) -> str:
    parts = []
    for node in paragraph.iter():
        if node.tag == f"{{{W_NS}}}t":
            parts.append(node.text or "")
        elif node.tag == f"{{{W_NS}}}tab":
            parts.append("\t")
        elif node.tag == f"{{{W_NS}}}br":
            kind = node.attrib.get(f"{{{W_NS}}}type")
            parts.append("\f" if kind == "page" else "\n")
        elif node.tag == f"{{{W_NS}}}lastRenderedPageBreak":
            parts.append("\f")
    return "".join(parts).strip()


def _split_sections(paragraphs: list[str]) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    current_length = 0
    total_chars = 0

    def flush():
        nonlocal current, current_length
        value = "\n".join(part for part in current if part).strip()
        if value:
            sections.append(value)
        current, current_length = [], 0

    for paragraph in paragraphs:
        total_chars += len(paragraph)
        if total_chars > MAX_TEXT_CHARACTERS:
            raise DocxImportError("The DOCX text exceeds the supported extraction limit.")
        for part in paragraph.split("\f"):
            cleaned = " ".join(part.split())
            while len(cleaned) > MAX_SECTION_CHARACTERS:
                take = cleaned.rfind(" ", 0, MAX_SECTION_CHARACTERS + 1)
                if take < MAX_SECTION_CHARACTERS // 2:
                    take = MAX_SECTION_CHARACTERS
                current.append(cleaned[:take])
                current_length += take
                cleaned = cleaned[take:].strip()
                flush()
            if cleaned:
                if current_length + len(cleaned) + 1 > MAX_SECTION_CHARACTERS:
                    flush()
                current.append(cleaned)
                current_length += len(cleaned) + 1
            if "\f" in paragraph:
                flush()
    flush()
    if not sections:
        raise DocxImportError("No readable paragraph or table text was found in the DOCX file.")
    if len(sections) > MAX_SECTIONS:
        raise DocxImportError(f"DOCX text exceeds the {MAX_SECTIONS}-section analysis limit.")
    return sections


def extract_sections(data: bytes) -> list[str]:
    """Read document body paragraphs/tables; never follows links, evaluates fields, or extracts macros."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES or sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
                raise DocxImportError("The DOCX package exceeds its safe expanded size limits.")
            names = [item.filename for item in infos]
            if len(set(names)) != len(names) or any(item.flag_bits & 0x1 for item in infos):
                raise DocxImportError("The DOCX package contains duplicate or encrypted parts.")
            if "word/vbaProject.bin" in names or any(name.startswith("word/embeddings/") for name in names):
                raise DocxImportError("Macro-enabled DOCX files and embedded OLE objects are not supported.")
            root_rels = _parse(archive, "_rels/.rels")
            main_part = next((rel for rel in root_rels.findall(f"{{{REL_NS}}}Relationship")
                              if rel.attrib.get("Type", "").endswith("/officeDocument")
                              and rel.attrib.get("TargetMode") != "External"), None)
            if main_part is None or posixpath.normpath(main_part.attrib.get("Target", "")) != "word/document.xml":
                raise DocxImportError("The DOCX package does not reference a supported document body.")
            content_types = _parse(archive, "[Content_Types].xml")
            declared = {item.attrib.get("PartName", "").lstrip("/") for item in content_types}
            if "word/document.xml" not in declared:
                raise DocxImportError("The DOCX package content types are incomplete.")
            root = _parse(archive, "word/document.xml")
            body = root.find(f".//{{{W_NS}}}body")
            if body is None:
                raise DocxImportError("The DOCX document has no readable body.")
            paragraphs = [_paragraph_text(node) for node in body.iter(f"{{{W_NS}}}p")]
            return _split_sections([value for value in paragraphs if value])
    except zipfile.BadZipFile as exc:
        raise DocxImportError("The file is not a valid DOCX OpenXML package.") from exc
