from datetime import date
from io import BytesIO
import struct
import zlib
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from backend.app import ai_engine, auth, document_processing, ocr, store
from backend.app.ocr import OCRPage


def make_xlsx():
    shared = ["Date", "Description", "Debit", "Credit", "Balance", "Salary"]
    shared_xml = "".join(f"<si><t>{value}</t></si>" for value in shared)
    cells = lambda row, values: "<row r=\"%s\">%s</row>" % (
        row, "".join(f"<c r=\"{col}{row}\" t=\"s\"><v>{index}</v></c>" for col, index in values)
    )
    first_row = "<row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>Statement export</t></is></c></row>"
    header = cells(3, [("A", 0), ("B", 1), ("C", 2), ("D", 3), ("E", 4)])
    serial = (date(2026, 5, 1) - date(1899, 12, 30)).days
    transaction = (
        f"<row r=\"4\"><c r=\"A4\" s=\"1\"><v>{serial}</v></c>"
        "<c r=\"B4\" t=\"s\"><v>5</v></c><c r=\"C4\"><v>0</v></c>"
        "<c r=\"D4\"><v>250.75</v></c><c r=\"E4\"><v>1250.75</v></c></row>"
    )
    workbook = b'''<?xml version="1.0" encoding="UTF-8"?>
      <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <workbookPr date1904="0"/><sheets><sheet name="Transactions" sheetId="1" r:id="rId1"/></sheets>
      </workbook>'''
    relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
      <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
      </Relationships>'''
    root_relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
      <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
      </Relationships>'''
    content_types = b'''<?xml version="1.0" encoding="UTF-8"?>
      <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
        <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
        <Default Extension="xml" ContentType="application/xml"/>
        <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
        <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
      </Types>'''
    worksheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + first_row + header + transaction + "</sheetData></worksheet>"
    ).encode()
    styles = b'''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <numFmts count="0"/><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs>
    </styleSheet>'''
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_relationships)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{shared_xml}</sst>')
        archive.writestr("xl/styles.xml", styles)
    return buffer.getvalue()


def make_png(width=200, height=100):
    def chunk(kind, data):
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_docx():
    root_relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
      <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
      </Relationships>'''
    content_types = b'''<?xml version="1.0" encoding="UTF-8"?>
      <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
        <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
        <Default Extension="xml" ContentType="application/xml"/>
        <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
      </Types>'''
    document = b'''<?xml version="1.0" encoding="UTF-8"?>
      <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:r><w:t>Home Loan Agreement</w:t></w:r></w:p>
        <w:p><w:r><w:t>Annual interest rate is 8.25 percent per annum.</w:t></w:r></w:p>
        <w:p><w:r><w:t>The borrower may repay the home loan early without a stated prepayment fee.</w:t></w:r></w:p>
      </w:body></w:document>'''
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_relationships)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def test_xlsx_statement_import_preserves_excel_row_and_sheet_evidence():
    result = document_processing.analyze_document_bytes(make_xlsx(), "statement.xlsx", "a" * 32)
    assert result["doc_type"] == "bank_statement_xlsx"
    assert result["transaction_count"] == 1
    transaction = result["transactions"][0]
    assert transaction["date"] == "2026-05-01"
    assert transaction["description"] == "Salary"
    assert transaction["credit_minor"] == 25075
    assert transaction["balance_minor"] == 125075
    assert transaction["row_number"] == 4
    assert transaction["source_sheet"] == "Transactions"
    assert result["statement_analysis"]["source_format"] == "xlsx"


def test_xlsx_rejects_macro_enabled_workbooks():
    data = make_xlsx()
    buffer = BytesIO(data)
    with ZipFile(BytesIO(data)) as source, ZipFile(buffer, "a", ZIP_DEFLATED) as target:
        target.writestr("xl/vbaProject.bin", b"not executed")
    with pytest.raises(document_processing.HTTPException) as raised:
        document_processing.analyze_document_bytes(buffer.getvalue(), "statement.xlsx", "b" * 32)
    assert raised.value.status_code == 422
    assert "Macro-enabled" in raised.value.detail


def test_scanned_png_uses_bounded_ocr_path_and_emits_page_evidence(monkeypatch):
    captured = {}
    text = "Home Loan Agreement\nLoan Amount: INR 800,000\nInterest Rate: 7.5% p.a."
    monkeypatch.setattr(ocr, "validate_settings", lambda: None)

    def fake_extract(data, indexes):
        captured["indexes"] = indexes
        captured["pdf_signature"] = data.startswith(b"%PDF")
        return {0: OCRPage(text, [{"text": "Loan Amount: INR 800,000", "bbox": [10, 20, 200, 40]}])}

    monkeypatch.setattr(ocr, "extract_pages", fake_extract)
    result = document_processing.analyze_document_bytes(make_png(), "loan.png", "c" * 32)
    assert captured == {"indexes": [0], "pdf_signature": True}
    assert result["doc_type"] == "loan_agreement"
    assert result["ocr_pages"] == [1]
    assert result["terms"]["loan_amount"]["page"] == 1
    assert result["terms"]["loan_amount"]["extraction_method"] == "pymupdf_tesseract_ocr"


@pytest.fixture
def upload_client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BANKEASE_ASYNC_DOCUMENTS", "false")
    from backend.app.main import app
    old = dict(app.dependency_overrides)
    app.dependency_overrides[auth.get_current_user] = lambda: {
        "id": "spreadsheet-owner", "name": "Test", "email": "owner@example.test", "email_verified": True,
    }
    app.dependency_overrides[auth.require_csrf] = lambda: None
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)


def test_xlsx_upload_keeps_original_private_and_download_authorized(upload_client):
    original = make_xlsx()
    uploaded = upload_client.post("/api/v1/documents", files={
        "file": ("statement.xlsx", original, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    })
    assert uploaded.status_code == 201, uploaded.text
    row = uploaded.json()
    transactions = upload_client.get(f"/api/v1/documents/{row['id']}/transactions").json()["transactions"]
    assert transactions[0]["row_number"] == 4
    assert transactions[0]["source_sheet"] == "Transactions"
    source = upload_client.get(f"/api/v1/documents/{row['id']}/preview")
    assert source.status_code == 200, source.text
    assert source.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert source.headers["content-disposition"] == "attachment"
    assert source.content == original


def test_image_upload_keeps_original_and_uses_authenticated_inline_preview(upload_client, monkeypatch):
    original = make_png()
    recognized = "Home Loan Agreement\nLoan Amount: INR 800,000\nInterest Rate: 7.5% p.a."
    monkeypatch.setattr(ocr, "extract_pages", lambda _data, _indexes: {
        0: OCRPage(recognized, [{"text": "Loan Amount: INR 800,000", "bbox": [10, 20, 200, 40]}])
    })
    uploaded = upload_client.post("/api/v1/documents", files={
        "file": ("loan.png", original, "image/png")
    })
    assert uploaded.status_code == 201, uploaded.text
    row = uploaded.json()
    assert row["doc_type"] == "loan_agreement" and row["status"] == "needs_review"
    source = upload_client.get(f"/api/v1/documents/{row['id']}/preview")
    assert source.status_code == 200
    assert source.headers["content-type"] == "image/png"
    assert source.headers["content-disposition"] == "inline"
    assert source.content == original


def test_queued_xlsx_upload_is_processed_by_document_worker(upload_client, monkeypatch):
    from backend.app import jobs
    from backend.app.auth_store import AuthRepository

    monkeypatch.setenv("BANKEASE_ASYNC_DOCUMENTS", "true")
    uploaded = upload_client.post("/api/v1/documents", files={
        "file": ("queued-statement.xlsx", make_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    })
    assert uploaded.status_code == 202, uploaded.text
    document_id = uploaded.json()["id"]
    repository = AuthRepository(root=store.ROOT)
    assert jobs.process_next(repository)
    assert upload_client.get(f"/api/v1/documents/{document_id}/status").json()["status"] == "completed"
    transactions = upload_client.get(f"/api/v1/documents/{document_id}/transactions").json()["transactions"]
    assert transactions[0]["row_number"] == 4
    assert transactions[0]["source_sheet"] == "Transactions"


def test_statement_chat_retrieves_transaction_rows_and_balance_check(upload_client, monkeypatch):
    # Keep this assertion deterministic even when a developer has Ollama installed.
    monkeypatch.setattr(ai_engine, "_ollama_models", lambda timeout=2.0: (False, []))
    uploaded = upload_client.post("/api/v1/documents", files={
        "file": ("statement.xlsx", make_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    })
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    conversation = upload_client.post("/api/v1/conversations", json={"document_id": document_id})
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["id"]

    answer = upload_client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "question": "Which transaction mentions salary?"
    })
    assert answer.status_code == 200, answer.text
    result = answer.json()["answer"]
    assert "Salary" in result["answer"]
    assert result["sources"][0]["source_sheet"] == "Transactions"
    assert result["sources"][0]["row_number"] == 4
    assert result["provider"] == "extractive"

    reconciliation = upload_client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "question": "Does this statement balance reconcile?"
    })
    assert reconciliation.status_code == 200, reconciliation.text
    result = reconciliation.json()["answer"]
    assert "reconcile" in result["answer"].casefold()
    assert any(source["kind"] == "calculation" for source in result["sources"])
    assert "not bank verification" in result["answer"].casefold()


def test_large_raster_image_is_rejected_before_ocr(monkeypatch):
    data = make_png(width=10_000, height=1)
    monkeypatch.setattr(ocr, "validate_settings", lambda: None)
    monkeypatch.setattr(ocr, "extract_pages", lambda *_args: pytest.fail("OCR must not run for oversized images"))
    with pytest.raises(document_processing.HTTPException) as raised:
        document_processing.analyze_document_bytes(data, "wide.png", "d" * 32)
    assert raised.value.status_code == 413
    assert "dimensions" in raised.value.detail


def test_docx_upload_stores_private_source_and_cites_sections_without_fake_pages(upload_client):
    original = make_docx()
    uploaded = upload_client.post("/api/v1/documents", files={
        "file": ("loan-terms.docx", original, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    })
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    assert document["doc_type"] == "unknown"
    assert document["suggested_doc_type"] == "loan_agreement"
    assert document["suggested_loan_category"] == "home"
    assert document["source_format"] == "docx"
    assert document["source_locator_type"] == "section"
    assert document["page_count"] == 0
    assert document["section_count"] == 1
    assert document["status"] == "needs_review"

    listed = upload_client.get("/api/v1/documents")
    assert listed.status_code == 200
    summary = next(item for item in listed.json()["documents"] if item["id"] == document["id"])
    assert summary["suggested_doc_type"] == "loan_agreement"
    assert summary["doc_type"] == "unknown"

    preview = upload_client.get(f"/api/v1/documents/{document['id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-disposition"] == "attachment"
    assert preview.content == original

    direct = upload_client.post(f"/api/v1/documents/{document['id']}/ask", json={
        "question": "What does the agreement say about prepayment fees?"
    })
    assert direct.status_code == 200, direct.text
    direct_source = direct.json()["sources"][0]
    assert direct_source["page"] is None
    assert direct_source["section"] == 1
    assert "page layout unavailable" in direct_source["location"]
    search = upload_client.get("/api/v1/search", params={"q": "prepayment fee"})
    assert search.status_code == 200
    section_result = next(item for item in search.json()["results"] if item["kind"] == "section")
    assert section_result["page"] is None
    assert section_result["location"] == "extracted text section 1"

    conversation = upload_client.post("/api/v1/conversations", json={"document_id": document["id"]})
    assert conversation.status_code == 201, conversation.text
    response = upload_client.post(f"/api/v1/conversations/{conversation.json()['id']}/messages", json={
        "question": "What does the agreement say about prepayment fees?"
    })
    assert response.status_code == 200, response.text
    answer = response.json()["answer"]
    assert "prepayment fee" in answer["answer"].casefold()
    assert answer["sources"][0]["page"] is None
    assert answer["sources"][0]["location"].startswith("extracted text section 1")


@pytest.mark.parametrize(("filename", "content", "media_type", "expected_format"), [
    ("notes.txt", b"Annual interest rate: 10.5 percent.", "text/plain", "txt"),
    ("terms.json", b'{"annual_interest_rate":"10.5 percent"}', "application/json", "json"),
    ("terms.jsonl", b'{"clause":"Annual interest rate: 10.5 percent."}\n', "application/x-ndjson", "jsonl"),
])
def test_plain_text_formats_upload_and_are_available_to_conversations(
        upload_client, filename, content, media_type, expected_format):
    uploaded = upload_client.post("/api/v1/documents", files={
        "file": (filename, content, media_type),
    })
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    assert document["status"] == "needs_review"
    assert document["doc_type"] == "unknown"
    assert document["source_format"] == expected_format
    assert document["source_locator_type"] == "section"

    conversation = upload_client.post("/api/v1/conversations", json={"document_ids": [document["id"]]})
    assert conversation.status_code == 201, conversation.text

    answer = upload_client.post(f"/api/v1/documents/{document['id']}/ask", json={
        "question": "What annual interest rate is stated?",
    })
    assert answer.status_code == 200, answer.text
    assert answer.json()["sources"][0]["page"] is None
    assert answer.json()["sources"][0]["section"] == 1


def test_json_upload_rejects_malformed_content(upload_client):
    response = upload_client.post("/api/v1/documents", files={
        "file": ("terms.json", b'{"annual_interest_rate":', "application/json"),
    })
    assert response.status_code == 415
    assert "not valid JSON" in response.text
