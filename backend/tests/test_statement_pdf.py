from io import BytesIO
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app import auth, store, main as main_module
from backend.app.extract import classify
from backend.app.main import app
from backend.app.statements import parse_statement_ocr_pages, parse_statement_pdf
import pymupdf


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path / "runtime")
    monkeypatch.setenv("APP_ENV", "test")
    old = dict(app.dependency_overrides)
    app.dependency_overrides[auth.get_current_user] = lambda: {
        "id": "pdf-statement-owner", "name": "Test", "email": "pdf-statement@example.test", "email_verified": True,
    }
    app.dependency_overrides[auth.require_csrf] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)


def make_statement_pdf(include_table=True, two_pages=False):
    output = BytesIO()
    story = [Paragraph("Bank Statement", getSampleStyleSheet()["Title"]),
             Paragraph("Account period: January 2026", getSampleStyleSheet()["Normal"]), Spacer(1, 16)]
    if include_table:
        rows = [
            ["Transaction Date", "Narration", "Debit", "Credit", "Running Balance", "Currency"],
            ["2026-01-01", "Salary credit", "", "100,000.00", "100,000.00", "INR"],
            ["2026-01-02", "Groceries", "4,250.00", "", "95,750.00", "INR"],
            ["2026-01-03", "Utilities", "2,000.00", "", "93,750.00", "INR"],
        ]
        table = Table(rows, colWidths=[88, 150, 75, 75, 100, 45], repeatRows=1)
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(table)
    if two_pages:
        story.extend([PageBreak(), Paragraph("Statement continuation for February 2026", getSampleStyleSheet()["Normal"])])
    SimpleDocTemplate(output, pagesize=letter).build(story)
    return output.getvalue()


def make_ocr_statement_words():
    words = []
    def line(line_id, y, items):
        for text, x0, x1 in items:
            words.append({"text": text, "bbox": [x0, y, x1, y + 8], "line_id": f"{line_id}-{len(words)}"})
    line("header", 100, [
        ("Transaction", 20, 80), ("Date", 85, 104), ("Description", 120, 185),
        ("Debit", 235, 280), ("Credit", 315, 365), ("Running", 400, 445),
        ("Balance", 450, 490), ("Currency", 505, 555),
    ])
    line("row-1", 120, [
        ("2026-01-01", 30, 90), ("Salary", 120, 170), ("credit", 175, 220),
        ("100,000.00", 315, 375), ("100,000.00", 405, 485), ("INR", 510, 540),
    ])
    line("row-2", 140, [
        ("2026-01-02", 30, 90), ("Groceries", 120, 195), ("4,250.00", 235, 285),
        ("95,750.00", 405, 485), ("INR", 510, 540),
    ])
    return words


def test_statement_classifier_does_not_match_a_loan_reference_to_a_bank_statement():
    dtype, _ = classify(["Loan Agreement\nBorrower shall provide a bank statement on request."])
    assert dtype == "loan_agreement"


def test_digital_pdf_statement_table_extracts_rows_with_page_evidence():
    with pymupdf.open(stream=make_statement_pdf(), filetype="pdf") as document:
        result = parse_statement_pdf(document)

    assert result["transaction_count"] == 3
    assert result["currency"] == "INR"
    assert result["statement_analysis"]["extraction_status"] == "completed"
    assert result["statement_analysis"]["balance_check"] == "consistent"
    assert result["transactions"][0]["page"] == 1
    assert "Salary credit" in result["transactions"][0]["evidence"]
    assert result["transactions"][1]["amount_minor"] == -425000


def test_ocr_statement_words_extract_rows_but_require_human_review():
    result = parse_statement_ocr_pages([{"page": 1, "words": make_ocr_statement_words()}])

    assert result["transaction_count"] == 2
    assert result["transactions"][0]["amount_minor"] == 10_000_000
    assert result["transactions"][1]["amount_minor"] == -425_000
    assert result["transactions"][0]["page"] == 1
    assert "Salary credit" in result["transactions"][0]["evidence"]
    assert result["transactions"][0]["bbox"] == [30.0, 120.0, 540.0, 128.0]
    assert result["transactions"][0]["extraction_method"] == "pymupdf_tesseract_ocr"
    assert result["statement_analysis"]["balance_check"] == "consistent"
    assert result["statement_analysis"]["extraction_status"] == "needs_review"
    assert "human verification" in result["statement_analysis"]["balance_note"]


def test_ocr_statement_page_without_recognized_header_does_not_guess_columns():
    result = parse_statement_ocr_pages([{"page": 1, "words": [
        {"text": "2026-01-01", "bbox": [10, 10, 80, 18], "line_id": "row"},
        {"text": "100.00", "bbox": [400, 10, 440, 18], "line_id": "row"},
    ]}])
    assert result["transaction_count"] == 0
    assert result["statement_analysis"]["extraction_status"] == "needs_review"
    assert "no recognized transaction header" in result["statement_analysis"]["balance_note"]


def test_ocr_statement_rows_keep_page_evidence_across_repeated_headers():
    first_page = make_ocr_statement_words()
    second_header = [
        {**word, "line_id": f"second-header-{index}"}
        for index, word in enumerate(first_page)
        if word["line_id"].startswith("header")
    ]
    second_row = [
        ("2026-01-03", 30, 90), ("Utilities", 120, 190), ("2,000.00", 235, 285),
        ("93,750.00", 405, 485), ("INR", 510, 540),
    ]
    second_page = second_header + [
        {"text": text, "bbox": [x0, 120, x1, 128], "line_id": f"second-row-{index}"}
        for index, (text, x0, x1) in enumerate(second_row)
    ]
    result = parse_statement_ocr_pages([
        {"page": 1, "words": first_page}, {"page": 2, "words": second_page},
    ])
    assert result["transaction_count"] == 3
    assert [row["page"] for row in result["transactions"]] == [1, 1, 2]
    assert result["statement_analysis"]["balance_check"] == "consistent"


def test_ocr_statement_changed_header_columns_abstain():
    words = make_ocr_statement_words()
    words.extend({
        **word,
        "bbox": [word["bbox"][0] + 20, 160, word["bbox"][2] + 20, 168],
        "line_id": f"changed-{index}",
    } for index, word in enumerate(words) if word["line_id"].startswith("header"))
    result = parse_statement_ocr_pages([{"page": 1, "words": words}])
    assert result["transaction_count"] == 0
    assert "table columns changed" in result["statement_analysis"]["balance_note"]


def test_ocr_statement_malformed_numeric_row_abstains_without_partial_import():
    words = make_ocr_statement_words()
    words = [{**word, "text": "NOISE"} if word["text"] == "4,250.00" else word for word in words]
    result = parse_statement_ocr_pages([{"page": 1, "words": words}])
    assert result["transaction_count"] == 0
    assert result["statement_analysis"]["extraction_status"] == "needs_review"
    assert "Amounts must use standard digits" in result["statement_analysis"]["balance_note"]


def test_scanned_statement_pdf_persists_cited_ocr_rows_for_review(client, tmp_path, monkeypatch):
    from backend.app import ocr

    document = pymupdf.open()
    page = document.new_page()
    image = b"P6\n2 2\n255\n" + bytes([255, 255, 255] * 4)
    page.insert_image(page.rect, stream=image)
    path = tmp_path / "scanned-statement.pdf"
    document.save(path)
    document.close()
    text = "Bank Statement\nTransaction Date Description Debit Credit Running Balance Currency\n2026-01-01 Salary credit 100000 INR"
    monkeypatch.setattr(main_module.ocr, "extract_pages", lambda _pdf, _indexes: {
        0: ocr.OCRPage(text, [], make_ocr_statement_words())
    })

    response = client.post(
        "/api/v1/documents",
        files={"file": ("scanned-statement.pdf", path.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    document_record = response.json()
    assert document_record["status"] == "needs_review"
    assert document_record["doc_type"] == "bank_statement_pdf"
    assert document_record["ocr_pages"] == [1]
    assert document_record["transaction_count"] == 2
    rows = client.get(f"/api/v1/documents/{document_record['id']}/transactions").json()["transactions"]
    assert rows[0]["page"] == 1
    assert "Salary credit" in rows[0]["evidence"]
    assert rows[0]["bbox"] == [30.0, 120.0, 540.0, 128.0]
    assert rows[0]["evidence_status"] == "source_mismatch"
    assert rows[0]["verification_status"] == "needs_review"
    assert rows[0]["review_status"] == "pending"
    detail = client.get(f"/api/v1/documents/{document_record['id']}").json()
    assert detail["statement_analysis"]["extraction_status"] == "needs_review"


def test_scanned_statement_keeps_readable_rows_and_marks_omitted_pages(client, tmp_path, monkeypatch):
    from backend.app import ocr

    document = pymupdf.open()
    image = b"P6\n2 2\n255\n" + bytes([255, 255, 255] * 4)
    for _ in range(2):
        document.new_page().insert_image(pymupdf.Rect(0, 0, 612, 792), stream=image)
    path = tmp_path / "partially-readable-statement.pdf"
    document.save(path)
    document.close()
    text = "Bank Statement\nTransaction Date Description Debit Credit Running Balance Currency"
    monkeypatch.setattr(main_module.ocr, "extract_pages", lambda _pdf, _indexes: {
        0: ocr.OCRPage(text, [], make_ocr_statement_words())
    })

    response = client.post(
        "/api/v1/documents",
        files={"file": ("partially-readable-statement.pdf", path.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["status"] == "needs_review"
    assert record["ocr_pages"] == [1]
    assert record["unreadable_pages"] == [2]
    assert record["transaction_count"] == 2
    detail = client.get(f"/api/v1/documents/{record['id']}").json()
    assert "pages 2 were unreadable and were omitted" in detail["statement_analysis"]["balance_note"]


def test_mixed_selectable_and_scanned_statement_pages_are_not_partially_combined(client, tmp_path, monkeypatch):
    from backend.app import ocr

    document = pymupdf.open()
    document.new_page().insert_text((50, 70), "Bank Statement - Account ending 1234 - January 2026")
    scanned_page = document.new_page()
    image = b"P6\n2 2\n255\n" + bytes([255, 255, 255] * 4)
    scanned_page.insert_image(scanned_page.rect, stream=image)
    path = tmp_path / "mixed-statement.pdf"
    document.save(path)
    document.close()
    text = "Bank Statement\nTransaction Date Description Debit Credit Running Balance Currency"
    monkeypatch.setattr(main_module.ocr, "extract_pages", lambda _pdf, _indexes: {
        1: ocr.OCRPage(text, [], make_ocr_statement_words())
    })

    response = client.post(
        "/api/v1/documents",
        files={"file": ("mixed-statement.pdf", path.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["doc_type"] == "bank_statement_pdf"
    assert record["status"] == "needs_review"
    assert record["ocr_pages"] == [2]
    assert record["transaction_count"] == 0
    detail = client.get(f"/api/v1/documents/{record['id']}").json()
    assert "This PDF mixes selectable-text and OCR pages" in detail["statement_analysis"]["balance_note"]


def test_ocr_geometry_output_and_document_word_limits_are_enforced(monkeypatch):
    import subprocess
    from backend.app import ocr

    def oversized_output(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, b"x" * (ocr.MAX_OCR_OUTPUT_BYTES + 1), b"")

    monkeypatch.setattr(ocr.subprocess, "run", oversized_output)
    with pytest.raises(ocr.OCRError, match="output exceeded"):
        ocr.extract_page(b"bounded-pdf", 0)

    monkeypatch.setattr(ocr, "extract_page", lambda *_args, **_kwargs: ocr.OCRPage("", [], [{}] * 30_000))
    with pytest.raises(ocr.OCRError, match="word-count limit"):
        ocr.extract_pages(b"bounded-pdf", [0, 1])


def test_pdf_statement_upload_persists_cited_rows(client):
    response = client.post(
        "/api/v1/documents",
        files={"file": ("statement.pdf", make_statement_pdf(), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    document = response.json()
    assert document["doc_type"] == "bank_statement_pdf"
    assert document["transaction_count"] == 3
    assert document["status"] == "completed"
    page = client.get(f"/api/v1/documents/{document['id']}/transactions")
    assert page.status_code == 200
    assert page.json()["transactions"][0]["page"] == 1
    assert "Salary credit" in page.json()["transactions"][0]["evidence"]


def test_statement_pdf_without_supported_table_is_saved_for_review(client):
    response = client.post(
        "/api/v1/documents",
        files={"file": ("statement.pdf", make_statement_pdf(include_table=False), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["doc_type"] == "bank_statement_pdf"
    assert item["status"] == "needs_review"
    assert item["transaction_count"] == 0
    record = client.get(f"/api/v1/documents/{item['id']}").json()
    assert record["statement_analysis"]["extraction_status"] == "needs_review"
    assert "No supported transaction table" in record["statement_analysis"]["balance_note"]


def test_pdf_page_limit_is_enforced_before_table_processing(client, monkeypatch):
    monkeypatch.setattr(main_module, "MAX_PDF_PAGES", 1)
    response = client.post(
        "/api/v1/documents",
        files={"file": ("statement.pdf", make_statement_pdf(two_pages=True), "application/pdf")},
    )
    assert response.status_code == 413
    assert "1-page limit" in response.json()["detail"]
