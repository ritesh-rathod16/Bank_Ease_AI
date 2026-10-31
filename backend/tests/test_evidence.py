from backend.app.evidence import enrich_terms, verify_passages, verify_statement_rows


def _loan_term(**changes):
    term = {
        "value": 500000.0,
        "raw": "INR 500,000",
        "page": 1,
        "evidence": "Loan Amount: INR 500,000",
        "status": "extracted",
        "extraction_method": "pymupdf_text",
    }
    term.update(changes)
    return term


def test_native_text_field_gets_stable_schema_and_verified_source_reference():
    terms = {"loan_amount": _loan_term()}

    enrich_terms(
        terms,
        "a" * 32,
        ["\nLoan Amount: INR 500,000\n"],
        "b" * 32,
        page_bounds=[(612, 792)],
    )

    field = terms["loan_amount"]
    assert field["id"] == field["field_id"]
    assert field["document_id"] == "a" * 32
    assert field["field_name"] == "loan_amount"
    assert field["normalized_value"] == "500000"
    assert field["data_type"] == "money"
    assert field["page_number"] == 1
    assert field["source_text"] == field["evidence"]
    assert field["evidence_ref"]
    assert field["evidence_status"] == "matched"
    assert field["verification_status"] == "verified"
    assert field["field_extraction_confidence"] is None
    assert field["confidence_calibration"] == "unavailable"
    assert field["schema_version"] == 1
    assert field["value"] == 500000.0  # Legacy API field is preserved.


def test_source_mismatch_demotes_extracted_value_without_inventing_evidence():
    terms = {"loan_amount": _loan_term()}

    enrich_terms(terms, "a" * 32, ["Loan Amount: INR 50,000"], "b" * 32)

    field = terms["loan_amount"]
    assert field["status"] == "review"
    assert field["verification_status"] == "needs_review"
    assert field["evidence_status"] == "source_mismatch"
    assert field["source_text"] is None
    assert field["evidence_ref"] is None
    assert field["evidence"] == "Loan Amount: INR 500,000"


def test_invalid_page_or_out_of_bounds_bbox_cannot_be_verified():
    invalid_page = {"loan_amount": _loan_term(page=3)}
    enrich_terms(invalid_page, "a" * 32, ["Loan Amount: INR 500,000"], "b" * 32)
    assert invalid_page["loan_amount"]["page_status"] == "invalid"
    assert invalid_page["loan_amount"]["verification_status"] == "needs_review"
    assert invalid_page["loan_amount"]["evidence_ref"] is None

    invalid_bbox = {"loan_amount": _loan_term(bbox=[0, 0, 700, 20])}
    enrich_terms(
        invalid_bbox,
        "a" * 32,
        ["Loan Amount: INR 500,000"],
        "b" * 32,
        page_bounds=[(612, 792)],
    )
    assert invalid_bbox["loan_amount"]["bbox_status"] == "invalid"
    assert invalid_bbox["loan_amount"]["evidence_status"] == "invalid_bbox"
    assert invalid_bbox["loan_amount"]["verification_status"] == "needs_review"
    assert invalid_bbox["loan_amount"]["bbox"] is None
    assert invalid_bbox["loan_amount"]["bbox_candidate"] == [0, 0, 700, 20]
    assert invalid_bbox["loan_amount"]["evidence_ref"] is None


def test_ocr_evidence_can_be_located_but_stays_in_human_review():
    terms = {"loan_amount": _loan_term(extraction_method="pymupdf_tesseract_ocr")}

    enrich_terms(terms, "a" * 32, ["Loan Amount: INR 500,000"], "b" * 32, ocr_pages=[1])

    field = terms["loan_amount"]
    assert field["evidence_status"] == "matched"
    assert field["evidence_ref"]
    assert field["verification_status"] == "needs_review"
    assert field["review_status"] == "pending"


def test_percentage_fee_keeps_a_decimal_normalized_value():
    terms = {"processing_fee": _loan_term(value="2.5%", raw="2.5%", evidence="Processing fee: 2.5%")}

    enrich_terms(terms, "a" * 32, ["Processing fee: 2.5%"], "b" * 32)

    field = terms["processing_fee"]
    assert field["data_type"] == "percentage"
    assert field["normalized_value"] == "2.5"


def test_passage_verification_requires_exact_source_page_text():
    passages = [{"page": 1, "text": "Borrower may repay early.", "evidence": "Borrower may repay early."}]

    verify_passages(passages, "a" * 32, ["Borrower may repay early."], page_bounds=[(612, 792)])

    assert passages[0]["verification_status"] == "verified"
    assert passages[0]["evidence_ref"]

    missing = [{"page": 2, "text": "Borrower may repay early.", "evidence": "Borrower may repay early."}]
    verify_passages(missing, "a" * 32, ["Borrower may repay early."])
    assert missing[0]["verification_status"] == "needs_review"
    assert missing[0]["evidence_ref"] is None


def test_statement_rows_distinguish_source_cells_from_pdf_citations():
    csv_rows = [{"row_number": 2, "source_values": {"Date": "2026-01-01", "Description": "Salary"}}]
    verify_statement_rows(csv_rows, "a" * 32, [], "b" * 32, "csv")
    assert csv_rows[0]["evidence_status"] == "source_cells_available"
    assert csv_rows[0]["verification_status"] == "extracted"
    assert csv_rows[0]["source_locator"] == {"type": "csv_row", "row_number": 2}

    pdf_rows = [{"row_number": 2, "page": 1, "evidence": "2026-01-01 | Salary"}]
    verify_statement_rows(pdf_rows, "a" * 32, ["2026-01-01 Salary"], "b" * 32, "pdf")
    assert pdf_rows[0]["evidence_status"] == "matched"
    assert pdf_rows[0]["verification_status"] == "verified"
