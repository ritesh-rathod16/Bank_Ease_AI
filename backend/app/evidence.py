"""Deterministic evidence checks for category-specific extraction results."""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import math
import re
import unicodedata

from .extraction_schema import ExtractedField


_MONEY_FIELDS = {
    "loan_amount", "disbursed_amount", "emi", "processing_fee", "late_fee",
    "sum_insured", "premium_amount", "total_assets", "total_liabilities_and_equity",
    "total_liabilities", "total_equity", "total_revenue", "total_income",
    "total_expenses", "profit_after_tax",
}
_PERCENT_FIELDS = {"interest_rate"}
_DATE_FIELDS = {
    "first_emi_date", "loan_maturity_date", "interest_reset_date", "policy_start_date",
    "policy_expiry_date", "date_of_birth", "report_date",
}
_INTEGER_FIELDS = {"tenure"}
_CONFLICT_MARKERS = ("conflict", "multiple differing", "multiple values")


def normalize_source_text(value: str) -> str:
    """Normalize layout whitespace and Unicode while retaining punctuation."""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(normalized.replace("\u00a0", " ").split())


def _decimal_string(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    return format(number.normalize(), "f") if number else "0"


def _data_type(field_name, term):
    if field_name in _DATE_FIELDS or field_name.endswith("_date"):
        return "date"
    if field_name in _PERCENT_FIELDS or ("%" in str(term.get("raw") or "") and field_name.endswith("fee")):
        return "percentage"
    if field_name in _MONEY_FIELDS or term.get("currency"):
        return "money"
    if field_name in _INTEGER_FIELDS:
        return "integer"
    if isinstance(term.get("value"), bool):
        return "unknown"
    if isinstance(term.get("value"), int):
        return "integer"
    if isinstance(term.get("value"), float):
        return "number"
    if term.get("value") is not None or term.get("raw") is not None:
        return "text"
    return "unknown"


def _currency(term):
    explicit = term.get("currency")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().upper()
    raw = str(term.get("raw") or "")
    match = re.search(r"(?i)(?<![A-Z])(?:INR|USD|EUR|GBP|AUD|CAD|JPY|US\$|Rs\.?)(?![A-Z])|[₹€£]", raw)
    if not match:
        return None
    token = match.group().casefold().rstrip(".")
    return {"rs": "INR", "us$": "USD", "₹": "INR", "€": "EUR", "£": "GBP"}.get(token, token.upper())


def _numeric_value(value):
    parsed = _decimal_string(value)
    if parsed is not None:
        return parsed
    match = re.search(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?", str(value or ""))
    return _decimal_string(match.group().replace(",", "")) if match else None


def _normalized_value(field_name, term, data_type):
    value = term.get("value")
    if value is None:
        return None
    if data_type == "percentage":
        return _numeric_value(term.get("raw") or value)
    if data_type in {"money", "number"}:
        return _numeric_value(term.get("raw") or value)
    if data_type == "date":
        return str(value)
    if data_type == "integer":
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None
    return str(value)


def _valid_bbox(value, page_bounds):
    if value is None:
        return "unavailable"
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return "invalid"
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError, OverflowError):
        return "invalid"
    if not all(math.isfinite(item) for item in (x0, y0, x1, y1)):
        return "invalid"
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        return "invalid"
    if page_bounds:
        width, height = page_bounds
        if x1 > width + 1 or y1 > height + 1:
            return "invalid"
    return "valid"


def _field_hash(document_id, field_name):
    return sha256(f"{document_id}\0{field_name}".encode("utf-8")).hexdigest()[:32]


def _evidence_hash(document_id, field_name, page_number, source_text):
    identity = f"{document_id}\0{field_name}\0{page_number}\0{normalize_source_text(source_text)}"
    return sha256(identity.encode("utf-8")).hexdigest()[:32]


def _verify_source(pages, page_number, evidence, raw_value):
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        return "invalid_page", "not_found", False
    if page_number > len(pages):
        return "invalid_page", "not_found", False
    if not isinstance(evidence, str) or not evidence.strip():
        return "not_found", "not_found", False
    source_page = normalize_source_text(pages[page_number - 1] or "")
    source_evidence = normalize_source_text(evidence)
    if not source_evidence or source_evidence not in source_page:
        return "source_mismatch", "mismatch", False
    raw_matches = isinstance(raw_value, str) and bool(normalize_source_text(raw_value))
    raw_matches = raw_matches and normalize_source_text(raw_value) in source_evidence
    if raw_value is not None and not raw_matches:
        return "source_mismatch", "mismatch", False
    return "matched", "matched", True


def _verify_row_source(pages, page_number, evidence):
    status, text_status, matched = _verify_source(pages, page_number, evidence, None)
    if matched or not isinstance(evidence, str) or "|" not in evidence:
        return status, text_status, matched
    if not isinstance(page_number, int) or isinstance(page_number, bool) or not 1 <= page_number <= len(pages):
        return status, text_status, matched
    source_page = normalize_source_text(pages[page_number - 1] or "")
    parts = [normalize_source_text(part) for part in evidence.split("|") if normalize_source_text(part)]
    if parts and all(part in source_page for part in parts):
        return "matched", "matched", True
    return status, text_status, matched


def enrich_terms(terms, document_id, pages, processing_run_id, ocr_pages=None, page_bounds=None):
    """Attach validated common metadata while keeping legacy term keys intact.

    Source evidence is never synthesized. A field is only marked verified when
    the exact passage and raw value can be located on the stated native-text page.
    """
    ocr_pages = set(ocr_pages or [])
    page_bounds = page_bounds or []
    for field_name, term in terms.items():
        if not isinstance(term, dict):
            continue
        page_number = term.get("page")
        evidence = term.get("evidence")
        raw_value = term.get("raw")
        evidence_status, source_text_status, source_matched = _verify_source(
            pages, page_number, evidence, raw_value,
        )
        page_status = "valid" if isinstance(page_number, int) and not isinstance(page_number, bool) and 1 <= page_number <= len(pages) else ("missing" if page_number is None else "invalid")
        page_size = page_bounds[page_number - 1] if page_status == "valid" and page_number <= len(page_bounds) else None
        bbox_status = _valid_bbox(term.get("bbox"), page_size)
        original_status = str(term.get("status", "missing"))
        note = str(term.get("note") or "").casefold()
        has_conflict = any(marker in note for marker in _CONFLICT_MARKERS)
        if bbox_status == "invalid":
            term.setdefault("bbox_candidate", term.get("bbox"))
            term["bbox"] = None
            evidence_status = "invalid_bbox"

        if original_status == "missing" or term.get("value") is None and not raw_value:
            verification_status = "not_found" if page_status == "missing" else "needs_review"
        elif has_conflict:
            verification_status = "conflicting"
        elif page_status != "valid" or not source_matched or bbox_status == "invalid":
            verification_status = "needs_review"
        elif original_status != "extracted":
            verification_status = "needs_review"
        elif page_number in ocr_pages:
            verification_status = "needs_review"
        else:
            verification_status = "verified"

        if verification_status in {"not_found", "needs_review", "conflicting", "failed"} and original_status == "extracted":
            term["status"] = "review"
            if not term.get("note"):
                term["note"] = "The extracted value needs review because its source evidence could not be fully verified."

        if original_status in {"missing", "review"} or verification_status in {"needs_review", "conflicting", "not_found", "failed"}:
            review_status = "pending"
        elif term.get("user_correction"):
            review_status = "corrected"
        else:
            review_status = "not_reviewed"

        validation_status = "not_run"
        if original_status == "missing":
            validation_status = "not_found"
        elif original_status == "review":
            validation_status = "needs_review"
        elif has_conflict:
            validation_status = "conflicting"

        data_type = _data_type(field_name, term)
        bbox = tuple(float(item) for item in term["bbox"]) if bbox_status == "valid" else None
        evidence_ref = (
            _evidence_hash(document_id, field_name, page_number, evidence)
            if source_matched and bbox_status != "invalid" else None
        )
        field_id = _field_hash(document_id, field_name)
        standard = ExtractedField(
            id=field_id,
            field_id=field_id,
            document_id=document_id,
            field_name=field_name,
            raw_value=raw_value,
            normalized_value=_normalized_value(field_name, term, data_type),
            data_type=data_type,
            currency=_currency(term),
            page_number=page_number if page_status == "valid" else None,
            source_text=evidence if source_matched else None,
            bbox=bbox,
            extraction_method=term.get("extraction_method"),
            ocr_confidence=None,
            field_extraction_confidence=None,
            evidence_ref=evidence_ref,
            evidence_status=evidence_status,
            source_text_status=source_text_status,
            page_status=page_status,
            bbox_status=bbox_status,
            verification_status=verification_status,
            validation_status=validation_status,
            review_status=review_status,
            processing_run_id=processing_run_id,
        )
        term.update(standard.model_dump(mode="json"))
    return terms


def verify_passages(passages, document_id, pages, ocr_pages=None, page_bounds=None):
    """Verify clause and report-passage citations without rewriting their text."""
    ocr_pages = set(ocr_pages or [])
    page_bounds = page_bounds or []
    for index, passage in enumerate(passages or []):
        if not isinstance(passage, dict):
            continue
        page_number = passage.get("page")
        evidence = passage.get("evidence") or passage.get("text")
        page_status = "valid" if isinstance(page_number, int) and not isinstance(page_number, bool) and 1 <= page_number <= len(pages) else ("missing" if page_number is None else "invalid")
        evidence_status, source_text_status, source_matched = _verify_source(pages, page_number, evidence, None)
        page_size = page_bounds[page_number - 1] if page_status == "valid" and page_number <= len(page_bounds) else None
        bbox_status = _valid_bbox(passage.get("bbox"), page_size)
        if bbox_status == "invalid":
            evidence_status = "invalid_bbox"
        verified = source_matched and page_status == "valid" and bbox_status != "invalid" and page_number not in ocr_pages
        reference = _evidence_hash(document_id, f"passage:{index}", page_number, evidence) if source_matched and bbox_status != "invalid" else None
        passage.update({
            "evidence_ref": reference,
            "evidence_status": evidence_status,
            "source_text_status": source_text_status,
            "page_status": page_status,
            "bbox_status": bbox_status,
            "source_text": evidence if source_matched else None,
            "verification_status": "verified" if verified else "needs_review",
            "review_status": "not_reviewed" if verified else "pending",
        })
    return passages


def verify_statement_rows(rows, document_id, pages, processing_run_id, source_format,
                          ocr_pages=None, page_bounds=None):
    """Attach row-level evidence state for CSV/XLSX cells and PDF table rows."""
    ocr_pages = set(ocr_pages or [])
    page_bounds = page_bounds or []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        row_number = row.get("row_number")
        page_number = row.get("page")
        source_values = row.get("source_values")
        if source_format in {"csv", "xlsx"} and isinstance(source_values, dict) and source_values and row_number is not None:
            locator_type = "worksheet_row" if source_format == "xlsx" else "csv_row"
            locator = {"type": locator_type, "row_number": row_number}
            if row.get("source_sheet"):
                locator["sheet"] = row["source_sheet"]
            evidence_text = "\0".join(f"{key}={source_values[key]}" for key in sorted(source_values))
            reference = _evidence_hash(document_id, f"transaction:{row_number}", locator, evidence_text)
            row.update({
                "evidence_ref": reference,
                "evidence_status": "source_cells_available",
                "verification_status": "extracted",
                "review_status": "not_reviewed",
                "source_locator": locator,
                "processing_run_id": processing_run_id,
            })
            continue

        evidence_text = row.get("evidence")
        evidence_status, source_text_status, source_matched = _verify_row_source(pages, page_number, evidence_text)
        page_status = "valid" if isinstance(page_number, int) and not isinstance(page_number, bool) and 1 <= page_number <= len(pages) else ("missing" if page_number is None else "invalid")
        page_size = page_bounds[page_number - 1] if page_status == "valid" and page_number <= len(page_bounds) else None
        bbox_status = _valid_bbox(row.get("bbox"), page_size)
        if bbox_status == "invalid":
            evidence_status = "invalid_bbox"
        verified = source_matched and page_status == "valid" and bbox_status != "invalid" and page_number not in ocr_pages
        reference = _evidence_hash(document_id, f"transaction:{row_number}", page_number, evidence_text) if source_matched and bbox_status != "invalid" else None
        row.update({
            "evidence_ref": reference,
            "evidence_status": evidence_status,
            "source_text_status": source_text_status,
            "page_status": page_status,
            "bbox_status": bbox_status,
            "verification_status": "verified" if verified else "needs_review",
            "review_status": "not_reviewed" if verified else "pending",
            "processing_run_id": processing_run_id,
        })
    return rows
