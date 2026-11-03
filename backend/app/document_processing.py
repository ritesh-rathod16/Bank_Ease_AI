"""Pure document parsing pipeline shared by synchronous tests and queued workers."""
import io
import json
import os
import uuid
import zipfile

import pymupdf
from fastapi import HTTPException

from . import audit_reports, docx_import, evidence, extract, financial_statements, insurance, kyc, ocr, spreadsheet_import, statements

MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "100"))
MAX_TEXT_CHARACTERS = 500_000
MAX_TEXT_SECTIONS = 100
MAX_SECTION_CHARACTERS = 4_000


def _contract_candidate(filename: str, doc_type: str, pages: list[str]) -> bool:
    if doc_type in extract.LOAN_DOCUMENT_TYPES | {"insurance_policy"}:
        return True
    name = filename.casefold()
    if any(term in name for term in ("agreement", "contract", "lease", "nda", "employment")):
        return True
    opening = " ".join(pages[:3]).casefold()
    markers = ("this agreement", "the parties", "whereas", "terms and conditions",
               "governing law", "indemnification", "termination", "effective date")
    return sum(marker in opening for marker in markers) >= 2


def detect_upload_format(data: bytes, filename: str) -> str:
    """Match an allow-listed extension to its actual file signature/container."""
    extension = os.path.splitext(filename.casefold())[1]
    signatures = {
        ".pdf": ("pdf", data.startswith(b"%PDF")),
        ".png": ("png", data.startswith(b"\x89PNG\r\n\x1a\n")),
        ".jpg": ("jpg", data.startswith(b"\xff\xd8\xff")),
        ".jpeg": ("jpeg", data.startswith(b"\xff\xd8\xff")),
        ".tif": ("tif", data.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"))),
        ".tiff": ("tiff", data.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"))),
    }
    if extension in signatures:
        file_format, matches = signatures[extension]
        if matches:
            return file_format
        if extension == ".pdf":
            raise HTTPException(415, "Only PDF files with a valid signature or supported CSV files are accepted.")
        raise HTTPException(415, f"The file content does not match its {extension} extension.")
    if extension == ".csv":
        return "csv"
    if extension == ".xlsx":
        try:
            if zipfile.is_zipfile(io.BytesIO(data)):
                return "xlsx"
        except (OSError, ValueError):
            pass
        raise HTTPException(415, "The file is not a valid XLSX OpenXML container.")
    if extension == ".docx":
        try:
            if zipfile.is_zipfile(io.BytesIO(data)):
                return "docx"
        except (OSError, ValueError):
            pass
        raise HTTPException(415, "The file is not a valid DOCX OpenXML container.")
    if extension in {".txt", ".json", ".jsonl"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(415, "Text, JSON, and JSONL uploads must use UTF-8 encoding.") from exc
        if "\x00" in text:
            raise HTTPException(415, "The uploaded text file contains binary data.")
        if extension == ".json":
            try:
                json.loads(text)
            except (json.JSONDecodeError, RecursionError) as exc:
                raise HTTPException(415, "The uploaded JSON file is not valid JSON.") from exc
        if extension == ".jsonl":
            try:
                for line in text.splitlines():
                    if line.strip():
                        json.loads(line)
            except (json.JSONDecodeError, RecursionError) as exc:
                raise HTTPException(415, "The uploaded JSONL file contains an invalid JSON line.") from exc
        return extension[1:]
    raise HTTPException(415, "Upload a PDF, PNG, JPEG, TIFF, DOCX, TXT, JSON, JSONL, supported bank statement CSV, or XLSX file.")


def _text_sections(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise HTTPException(422, "No readable text was found in the uploaded file.")
    if len(normalized) > MAX_TEXT_CHARACTERS:
        raise HTTPException(413, "The extracted text exceeds the supported 500,000-character limit.")
    sections = []
    while normalized:
        if len(normalized) <= MAX_SECTION_CHARACTERS:
            chunk, normalized = normalized, ""
        else:
            boundary = normalized.rfind("\n", 0, MAX_SECTION_CHARACTERS + 1)
            if boundary < MAX_SECTION_CHARACTERS // 2:
                boundary = normalized.rfind(" ", 0, MAX_SECTION_CHARACTERS + 1)
            if boundary < MAX_SECTION_CHARACTERS // 2:
                boundary = MAX_SECTION_CHARACTERS
            chunk, normalized = normalized[:boundary], normalized[boundary:].lstrip()
        if chunk.strip():
            sections.append(chunk.strip())
        if len(sections) > MAX_TEXT_SECTIONS:
            raise HTTPException(413, f"The text exceeds the supported {MAX_TEXT_SECTIONS}-section analysis limit.")
    return sections


def analyze_document_bytes(data: bytes, filename: str, document_id: str, max_pdf_pages: int | None = None) -> dict:
    """Parse a previously bounded upload and return its complete analysis record."""
    page_limit = MAX_PDF_PAGES if max_pdf_pages is None else max_pdf_pages
    upload_format = detect_upload_format(data, filename)
    processing_run_id = uuid.uuid4().hex
    if upload_format == "docx":
        try:
            sections = docx_import.extract_sections(data)
        except docx_import.DocxImportError as exc:
            raise HTTPException(422, str(exc)) from exc
        suggested_doc_type, suggested_loan_category = extract.classify(sections)
        return {
            "id": document_id, "filename": filename, "status": "needs_review", "doc_type": "unknown",
            "suggested_doc_type": suggested_doc_type if suggested_doc_type != "unknown" else None,
            "suggested_loan_category": suggested_loan_category,
            "loan_category": None, "page_count": 0, "section_count": len(sections),
            "source_locator_type": "section", "source_format": "docx",
            "unreadable_pages": [], "ocr_pages": [], "terms": {}, "clauses": [], "pages": sections,
            "contract_candidate": _contract_candidate(filename, suggested_doc_type, sections),
            "processing_run_id": processing_run_id, "extraction_schema_version": 1,
            "analysis_note": "Text was extracted from the DOCX body for evidence-based questions. The suggested document type is a rule-based hint from that text and has not been verified. Word page layout is not preserved, so citations use extracted section numbers rather than page numbers. Structured banking fields were not verified; review the original document.",
        }
    if upload_format in {"txt", "json", "jsonl"}:
        try:
            text = data.decode("utf-8-sig")
            if upload_format == "json":
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            elif upload_format == "jsonl":
                text = "\n".join(json.dumps(json.loads(line), ensure_ascii=False)
                                  for line in text.splitlines() if line.strip())
            sections = _text_sections(text)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise HTTPException(422, "The uploaded text could not be safely decoded or parsed.") from exc
        suggested_doc_type, suggested_loan_category = extract.classify(sections)
        return {
            "id": document_id, "filename": filename, "status": "needs_review", "doc_type": "unknown",
            "suggested_doc_type": suggested_doc_type if suggested_doc_type != "unknown" else None,
            "suggested_loan_category": suggested_loan_category,
            "loan_category": None, "page_count": 0, "section_count": len(sections),
            "source_locator_type": "section", "source_format": upload_format,
            "unreadable_pages": [], "ocr_pages": [], "terms": {}, "clauses": [], "pages": sections,
            "contract_candidate": _contract_candidate(filename, suggested_doc_type, sections),
            "processing_run_id": processing_run_id, "extraction_schema_version": 1,
            "analysis_note": "Text was extracted into numbered sections for evidence-based questions. Page layout and structured financial fields are not verified; review findings against the original file.",
        }
    if upload_format in {"xlsx"}:
        try:
            imported = spreadsheet_import.parse_statement_xlsx(data)
        except spreadsheet_import.SpreadsheetImportError as exc:
            raise HTTPException(422, str(exc)) from exc
        analysis = imported["statement_analysis"]
        imported["transactions"] = evidence.verify_statement_rows(
            imported["transactions"], document_id, [], processing_run_id, "xlsx",
        )
        return {
            "id": document_id, "filename": filename, "status": "completed",
            "doc_type": "bank_statement_xlsx", "loan_category": "bank_statement",
            "page_count": 0, "unreadable_pages": [], "ocr_pages": [], "terms": {}, "clauses": [], "pages": [],
            "processing_run_id": processing_run_id, "extraction_schema_version": 1,
            "transactions": imported["transactions"], "transaction_count": imported["transaction_count"],
            "currency": imported["currency"], "reconciliation_status": analysis["balance_check"],
            "statement_analysis": analysis,
        }
    if upload_format in {"csv"}:
        try:
            imported = statements.parse_statement_csv(data)
        except statements.StatementImportError as exc:
            raise HTTPException(422, str(exc)) from exc
        analysis = imported["statement_analysis"]
        imported["transactions"] = evidence.verify_statement_rows(
            imported["transactions"], document_id, [], processing_run_id, "csv",
        )
        return {
            "id": document_id, "filename": filename, "status": "completed",
            "doc_type": "bank_statement_csv", "loan_category": "bank_statement",
            "page_count": 0, "unreadable_pages": [], "ocr_pages": [], "terms": {}, "clauses": [], "pages": [],
            "processing_run_id": processing_run_id, "extraction_schema_version": 1,
            "transactions": imported["transactions"], "transaction_count": imported["transaction_count"],
            "currency": imported["currency"], "reconciliation_status": analysis["balance_check"],
            "statement_analysis": analysis,
        }
    image_formats = {"png", "jpg", "jpeg", "tif", "tiff"}
    source_data = data
    if upload_format in image_formats:
        try:
            ocr.validate_settings()
            with pymupdf.open(stream=data, filetype=upload_format) as image:
                if len(image) < 1 or len(image) > page_limit:
                    raise HTTPException(413, f"Image document exceeds the {page_limit}-page limit.")
                scale = ocr.OCR_DPI / 72
                for page in image:
                    if (page.rect.width * scale > 6_000 or page.rect.height * scale > 6_000
                            or page.rect.width * page.rect.height * scale * scale > 25_000_000):
                        raise HTTPException(413, "This image exceeds the safe OCR dimensions. Resize it and upload again.")
                source_data = image.convert_to_pdf()
        except ocr.OCRError as exc:
            raise HTTPException(503, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, "The image could not be decoded safely.") from exc
    if upload_format == "pdf" or upload_format in image_formats:
        record = {"id": document_id, "filename": filename, "status": "processing"}
        try:
            with pymupdf.open(stream=source_data, filetype="pdf") as document:
                if len(document) > page_limit:
                    raise HTTPException(413, f"PDF exceeds the {page_limit}-page limit.")
                pages = [page.get_text() for page in document]
                page_bounds = [(float(page.rect.width), float(page.rect.height)) for page in document]
                empty_indexes = [index for index, text in enumerate(pages) if len(text.strip()) < 20]
                ocr_candidates = [index for index in empty_indexes if document[index].get_images()]
                if ocr_candidates:
                    try:
                        ocr.validate_settings()
                    except ocr.OCRError as exc:
                        raise HTTPException(503, str(exc)) from exc
                    if len(ocr_candidates) > ocr.OCR_MAX_PAGES:
                        raise HTTPException(413, f"Scanned PDF exceeds the {ocr.OCR_MAX_PAGES}-page OCR limit.")
                    try:
                        ocr_results = ocr.extract_pages(source_data, ocr_candidates)
                    except ocr.OCREngineUnavailable as exc:
                        raise HTTPException(503, str(exc)) from exc
                    ocr_pages, ocr_lines = [], {}
                    for index, result in ocr_results.items():
                        if len(result.text.strip()) >= 20:
                            pages[index] = result.text
                            ocr_pages.append(index + 1)
                            ocr_lines[index + 1] = result.lines
                else:
                    ocr_pages, ocr_lines = [], {}
                unreadable = [index + 1 for index, text in enumerate(pages) if len(text.strip()) < 20]
                if not pages or len(unreadable) == len(pages):
                    if ocr_candidates and not ocr_pages:
                        raise HTTPException(422, "No selectable text or readable OCR text was found within configured limits. Check scan quality and Tesseract language data.")
                    raise HTTPException(422, "No readable text was found. Empty pages without embedded images cannot be OCR-processed.")
                doc_type, category = extract.classify(pages)
                if doc_type == "bank_statement":
                    has_selectable_pages = any(index not in empty_indexes for index in range(len(pages)))
                    if ocr_pages and not has_selectable_pages:
                        imported = statements.parse_statement_ocr_pages([
                            {"page": index + 1, "words": result.words}
                            for index, result in ocr_results.items()
                            if index + 1 in ocr_pages
                        ])
                        if unreadable:
                            imported["statement_analysis"]["extraction_status"] = "needs_review"
                            imported["statement_analysis"]["balance_note"] += (
                                " Scanned pages " + ", ".join(map(str, unreadable))
                                + " were unreadable and were omitted; review the original before relying on these rows."
                            )
                    else:
                        imported = statements.parse_statement_pdf(document)
                        if ocr_pages:
                            imported["statement_analysis"]["extraction_status"] = "needs_review"
                            imported["statement_analysis"]["balance_note"] += (
                                " This PDF mixes selectable-text and OCR pages, or contains an unreadable scan; "
                                "table rows across those pages are not combined automatically."
                            )
                        elif unreadable:
                            imported["statement_analysis"]["extraction_status"] = "needs_review"
                            imported["statement_analysis"]["balance_note"] += (
                                " Unreadable pages " + ", ".join(map(str, unreadable))
                                + " were omitted from table extraction."
                            )
                else:
                    imported = None
            if not ocr_pages:
                ocr_lines = {}
            if doc_type == "bank_statement":
                transactions = evidence.verify_statement_rows(
                    imported["transactions"], document_id, pages, processing_run_id, "pdf",
                    ocr_pages, page_bounds,
                )
                row_evidence_needs_review = any(
                    row.get("verification_status") == "needs_review" for row in transactions
                )
                if row_evidence_needs_review:
                    imported["statement_analysis"]["extraction_status"] = "needs_review"
                    imported["statement_analysis"]["balance_note"] += (
                        " One or more transaction source citations could not be verified against the stated page."
                    )
                record.update(
                    status="completed" if imported["transaction_count"] and not (ocr_pages or unreadable or row_evidence_needs_review) else "needs_review",
                    page_count=len(pages), unreadable_pages=unreadable, ocr_pages=ocr_pages, doc_type="bank_statement_pdf",
                    loan_category="bank_statement", pages=[], terms={}, clauses=[],
                    transactions=transactions, transaction_count=imported["transaction_count"],
                    currency=imported["currency"],
                    reconciliation_status=imported["statement_analysis"]["balance_check"],
                    statement_analysis=imported["statement_analysis"],
                    processing_run_id=processing_run_id, extraction_schema_version=1,
                )
            elif doc_type == "unknown":
                reason = "Readable text was extracted, but this file was not classified as a supported loan document or bank statement. Structured financial analysis is unavailable."
                if ocr_pages or unreadable:
                    reason += " Some pages also need manual review because OCR or text extraction may be incomplete."
                record.update(status="needs_review", pages=pages, page_count=len(pages),
                              unreadable_pages=unreadable, ocr_pages=ocr_pages,
                              doc_type="unknown", loan_category=None, terms={}, clauses=[],
                              analysis_note=reason)
            else:
                if doc_type == "insurance_policy":
                    terms = insurance.extract_policy_terms(pages)
                    clauses = insurance.extract_policy_clauses(pages)
                elif doc_type == "kyc_form":
                    terms = kyc.extract_kyc_fields(pages)
                    clauses = []
                elif doc_type == "financial_statement":
                    terms = financial_statements.extract_fields(pages)
                    clauses = []
                elif doc_type == "audit_report":
                    terms = audit_reports.extract_terms(pages)
                    clauses = audit_reports.extract_passages(pages)
                else:
                    terms = extract.extract_terms(pages)
                    clauses = extract.extract_clauses(pages)
                for term in terms.values():
                    page_number = term.get("page")
                    term["extraction_method"] = "pymupdf_tesseract_ocr" if page_number in ocr_pages else "pymupdf_text"
                    if page_number in ocr_pages and term.get("evidence"):
                        matches = [item.get("bbox") for item in ocr_lines.get(page_number, [])
                                   if item.get("bbox") and item.get("text", "").strip().casefold() in term["evidence"].casefold()]
                        if matches:
                            term["bbox"] = [min(box[0] for box in matches), min(box[1] for box in matches),
                                            max(box[2] for box in matches), max(box[3] for box in matches)]
                terms = evidence.enrich_terms(
                    terms, document_id, pages, processing_run_id, ocr_pages, page_bounds,
                )
                clauses = evidence.verify_passages(clauses, document_id, pages, ocr_pages, page_bounds)
                kyc_needs_review = doc_type == "kyc_form" and any(
                    term.get("status") in {"missing", "review"} for term in terms.values()
                )
                financial_checks = financial_statements.verify_totals(terms) if doc_type == "financial_statement" else []
                financial_needs_review = doc_type == "financial_statement" and (
                    not any(check.get("status") == "consistent" for check in financial_checks)
                    or any(term.get("status") == "review" for term in terms.values())
                )
                audit_needs_review = doc_type == "audit_report"
                evidence_needs_review = any(
                    term.get("value") is not None
                    and term.get("verification_status") in {"needs_review", "conflicting", "failed"}
                    for term in terms.values()
                ) or any(passage.get("verification_status") == "needs_review" for passage in clauses)
                record.update(status="needs_review" if unreadable or ocr_pages or kyc_needs_review or financial_needs_review or audit_needs_review or evidence_needs_review else "completed", pages=pages,
                              page_count=len(pages), unreadable_pages=unreadable, ocr_pages=ocr_pages,
                              doc_type=doc_type, loan_category=category, terms=terms,
                              clauses=clauses, financial_checks=financial_checks,
                              processing_run_id=processing_run_id, extraction_schema_version=1)
                if doc_type == "insurance_policy":
                    record["analysis_note"] = "Insurance fields and policy passages are rule-detected from labeled source text. Confirm coverage, exclusions, limits, and dates against the original policy; extraction does not verify active coverage or claim eligibility."
                elif doc_type == "kyc_form":
                    record["analysis_note"] = (
                        "Only explicitly labeled name, birth date, and address text is extracted from this KYC form. "
                        "Government identity numbers are not extracted. OCR and field extraction do not verify a person's identity, "
                        "document authenticity, or eligibility; review every field against the original."
                    )
                elif doc_type == "financial_statement":
                    record["analysis_note"] = (
                        "Rule-based extraction covers only explicitly labeled totals. The balance-sheet check uses values "
                        "shown on the same page and compares the displayed amounts without assuming currency or scale. "
                        "Comparative columns, mixed units, layout variations, and accounting interpretation require human review."
                    )
                elif doc_type == "audit_report":
                    record["analysis_note"] = (
                        "Only explicitly labeled metadata and passages under recognized audit-report headings are shown. "
                        "Passages are quoted from the source, not independently interpreted; read the complete report and "
                        "management response. A reported qualification or finding is not a determination of misconduct."
                    )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, str(exc)[:200]) from exc
        record.setdefault("processing_run_id", processing_run_id)
        record.setdefault("extraction_schema_version", 1)
        record["contract_candidate"] = _contract_candidate(
            filename, record.get("doc_type", "unknown"), record.get("pages") or [])
        return record

    raise HTTPException(415, "The uploaded document format is not supported.")
