import sys, pathlib, pytest
from datetime import date, timedelta
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from fastapi.testclient import TestClient
from backend.app import auth, financial, store
from scripts.make_synthetic import make

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    from backend.app.main import app
    old = dict(app.dependency_overrides)
    app.dependency_overrides[auth.get_current_user] = lambda: {"id": "test-owner", "name": "Test", "email": "test@example.test", "email_verified": True}
    app.dependency_overrides[auth.require_csrf] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)

def up(client, path):
    with open(path, "rb") as f:
        return client.post("/documents/analyze", files={"file": (path.name, f, "application/pdf")})

@pytest.fixture()
def doc(client, tmp_path):
    make(tmp_path / "a.pdf"); r = up(client, tmp_path / "a.pdf"); assert r.status_code == 201
    return r.json()["id"]

# --- financial engine (expected values computed independently) ---
def test_emi_standard(): assert financial.emi(500000, 12, 36) == 16607.15
def test_emi_zero_interest(): assert financial.emi(120000, 0, 12) == 10000.0
def test_emi_uses_decimal_rounding_for_fractional_zero_interest():
    from decimal import Decimal
    assert financial.emi(Decimal("1000.01"), Decimal("0"), 3) == 333.34

@pytest.mark.parametrize("principal,rate,months", [(float("inf"), 1, 12), (1000, float("nan"), 12)])
def test_emi_rejects_non_finite_financial_inputs(principal, rate, months):
    with pytest.raises(financial.FinancialError): financial.emi(principal, rate, months)

def test_annual_amortization_sums_to_principal_and_exposes_actual_ending_balance():
    result = financial.summary(120000, 0, 120)
    schedule = result["annual_schedule"]
    assert len(schedule) == 10
    assert schedule[0] == {"year": 1, "principal_paid": 12000.0, "interest_paid": 0.0, "ending_balance": 108000.0}
    assert sum(year["principal_paid"] for year in schedule) == 120000.0
    assert schedule[-1]["ending_balance"] == 0.0
@pytest.mark.parametrize("a", [(0, 10, 12), (1000, 10, 0), (1000, -1, 12), (None, 10, 12), (1000, 10, 6.5)])
def test_emi_invalid(a):
    with pytest.raises(financial.FinancialError): financial.emi(*a)

# --- API / extraction ---
def test_health(client): assert client.get("/health").json()["status"] == "ok"
def test_rejects_non_pdf(client, tmp_path):
    (tmp_path / "x.pdf").write_bytes(b"not a pdf"); assert up(client, tmp_path / "x.pdf").status_code == 415
def test_rejects_empty_pdf(client, tmp_path):
    import pymupdf; d = pymupdf.open(); d.new_page(); d.save(tmp_path / "e.pdf")
    assert up(client, tmp_path / "e.pdf").status_code == 422

def test_unclassified_pdf_is_not_reported_as_structurally_analyzed(client, tmp_path):
    import pymupdf
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Household budget worksheet and family contact instructions.")
    path = tmp_path / "household-notes.pdf"
    document.save(path)
    document.close()
    uploaded = up(client, path)
    assert uploaded.status_code == 201, uploaded.text
    record = uploaded.json()
    assert record["doc_type"] == "unknown"
    assert record["status"] == "needs_review"
    assert "Structured financial analysis is unavailable" in record["analysis_note"]
    assert client.get(f"/api/v1/documents/{record['id']}/fields").json() == {}
    rejected = client.post("/api/v1/conversations", json={"document_id": record["id"], "title": "Question"})
    assert rejected.status_code == 409


def test_loan_dates_need_owner_confirmation_before_calendar_listing(client, tmp_path):
    import pymupdf

    first_emi = date.today() + timedelta(days=90)
    maturity = first_emi + timedelta(days=3650)
    page_text = (
        "Loan Agreement\nHome Loan\nLoan Amount: INR 2,000,000\nRate of Interest: 8.5% p.a.\n"
        "Tenure: 240 months\nEMI: INR 17,357\n"
        f"First EMI due date: {first_emi.strftime('%d %B %Y')}\n"
        f"Loan maturity date: {maturity.strftime('%d %B %Y')}\n"
        "Interest reset date: 05/06/2028"
    )
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), page_text)
    path = tmp_path / "dated-home-loan.pdf"
    pdf.save(path)
    pdf.close()

    uploaded = up(client, path)
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    terms = client.get(f"/api/v1/documents/{document_id}/fields").json()
    assert terms["first_emi_date"]["value"] == first_emi.isoformat()
    assert terms["first_emi_date"]["status"] == "extracted"
    assert terms["first_emi_date"]["page"] == 1
    assert terms["first_emi_date"]["evidence"] == f"First EMI due date: {first_emi.strftime('%d %B %Y')}"
    assert terms["loan_maturity_date"]["value"] == maturity.isoformat()
    assert terms["interest_reset_date"]["value"] is None
    assert terms["interest_reset_date"]["status"] == "review"
    assert client.get("/api/v1/dashboard/summary").json()["upcoming_obligations"] == []

    date_path = f"/api/v1/documents/{document_id}/fields/first_emi_date"
    invalid = client.patch(date_path, json={"value": first_emi.strftime("%d/%m/%Y")})
    assert invalid.status_code == 422
    past = client.patch(date_path, json={"value": (date.today() - timedelta(days=1)).isoformat()})
    assert past.status_code == 200
    assert client.get("/api/v1/dashboard/summary").json()["upcoming_obligations"] == []
    assert client.patch(date_path, json={"value": None}).status_code == 200
    confirmed = client.patch(date_path, json={
        "value": first_emi.isoformat(), "reason": "Confirmed against the signed repayment schedule."
    })
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["value"] == terms["first_emi_date"]["value"]
    assert confirmed.json()["effective_value"] == first_emi.isoformat()
    assert confirmed.json()["evidence"] == terms["first_emi_date"]["evidence"]

    obligations = client.get("/api/v1/dashboard/summary").json()["upcoming_obligations"]
    assert len(obligations) == 1
    assert obligations[0]["document_id"] == document_id
    assert obligations[0]["due_date"] == first_emi.isoformat()
    assert obligations[0]["confirmed_by_owner"] is True
    assert obligations[0]["date_page"] == 1
    assert obligations[0]["amount_label"] == "Stated EMI"
    assert obligations[0]["amount"] == 17357
    assert obligations[0]["amount_page"] == 1 and obligations[0]["amount_evidence"]
    calculation = client.post(f"/api/v1/documents/{document_id}/calculate-emi").json()
    assert not any("first emi date" in item.lower() for item in calculation["assumptions"])

    from backend.app.main import app
    original_user = app.dependency_overrides[auth.get_current_user]
    app.dependency_overrides[auth.get_current_user] = lambda: {"id": "unrelated-owner", "email": "other@example.test"}
    try:
        assert client.get("/api/v1/dashboard/summary").json()["upcoming_obligations"] == []
    finally:
        app.dependency_overrides[auth.get_current_user] = original_user

    reverted = client.patch(date_path, json={"value": None, "reason": "Recheck the source date."})
    assert reverted.status_code == 200
    assert client.get("/api/v1/dashboard/summary").json()["upcoming_obligations"] == []


def test_conflicting_contract_dates_do_not_choose_a_date():
    from backend.app import extract

    terms = extract.extract_terms([
        "Loan Agreement\nFirst EMI due date: 15 April 2027\nFirst EMI due date: 15 May 2027"
    ])
    term = terms["first_emi_date"]
    assert term["value"] is None
    assert term["status"] == "review"
    assert "2027-04-15" in term["note"] and "2027-05-15" in term["note"]
    assert "page 1: First EMI due date: 15 April 2027" in term["note"]
    assert len(term["candidates"]) == 2
    assert term["page"] == 1 and "First EMI due date: 15 April 2027" == term["evidence"]

def test_scanned_pdf_ocr_evidence_and_page_coordinates(client, tmp_path, monkeypatch):
    import pymupdf
    from backend.app import main as main_module, ocr
    document = pymupdf.open()
    page = document.new_page()
    image = b"P6\n2 2\n255\n" + bytes([255, 255, 255] * 4)
    page.insert_image(page.rect, stream=image)
    path = tmp_path / "scanned-loan.pdf"
    document.save(path)
    document.close()
    expected = "Loan Agreement\nPersonal Loan\nLoan Amount: INR 500,000\nRate of interest: 12% p.a.\nTenure: 36 months\nEMI: INR 16,607"
    monkeypatch.setattr(main_module.ocr, "extract_pages", lambda _pdf, indexes: {
        0: ocr.OCRPage(expected, [{"text": "Loan Amount: INR 500,000", "bbox": [32.0, 48.0, 221.0, 66.0]}])
    })
    uploaded = up(client, path)
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["ocr_pages"] == [1]
    assert uploaded.json()["status"] == "needs_review"
    term = client.get(f"/api/v1/documents/{uploaded.json()['id']}/fields").json()["loan_amount"]
    assert term["value"] == 500000
    assert term["extraction_method"] == "pymupdf_tesseract_ocr"
    assert term["bbox"] == [32.0, 48.0, 221.0, 66.0]

def test_scanned_pdf_reports_missing_tesseract_clearly(client, tmp_path, monkeypatch):
    import pymupdf
    from backend.app import main as main_module, ocr
    document = pymupdf.open()
    document.new_page().insert_image(pymupdf.Rect(0, 0, 612, 792), stream=b"P6\n2 2\n255\n" + bytes([255, 255, 255] * 4))
    path = tmp_path / "scanned.pdf"
    document.save(path)
    document.close()
    def missing_engine(_pdf, _indexes):
        raise ocr.OCREngineUnavailable("Scanned PDF detected, but Tesseract OCR is unavailable.")
    monkeypatch.setattr(main_module.ocr, "extract_pages", missing_engine)
    response = up(client, path)
    assert response.status_code == 503
    assert "Tesseract OCR is unavailable" in response.json()["detail"]

def test_ocr_subprocess_maps_engine_error_and_enforces_timeout(monkeypatch):
    import subprocess
    from backend.app import ocr
    def no_engine(command, **kwargs):
        assert kwargs["input"] == b"bounded-pdf-bytes"
        assert kwargs["timeout"] <= ocr.OCR_PAGE_TIMEOUT_SECONDS
        return subprocess.CompletedProcess(command, 1, b'{"error":true,"error_code":"engine_unavailable"}', b"")
    monkeypatch.setattr(ocr.subprocess, "run", no_engine)
    with pytest.raises(ocr.OCREngineUnavailable):
        ocr.extract_page(b"bounded-pdf-bytes", 0)

    def timed_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(ocr.subprocess, "run", timed_out)
    with pytest.raises(ocr.OCRPageTimeout):
        ocr.extract_page(b"bounded-pdf-bytes", 0)
def test_bad_id_404(client): assert client.get("/documents/../../etc/passwd").status_code in (404, 422)
def test_extraction_and_pages(client, doc):
    t = client.get(f"/documents/{doc}/terms").json()
    assert (t["loan_amount"]["value"], t["interest_rate"]["value"], t["tenure"]["value"], t["emi"]["value"]) == (500000, 12.0, 36, 16607)
    assert t["loan_amount"]["page"] == 1 and "Loan Amount" in t["loan_amount"]["evidence"]
    assert t["interest_type"]["value"] == "fixed"
    assert client.get(f"/documents/{doc}").json()["doc_type"] == "loan_agreement"

def test_common_evidence_metadata_is_exposed_through_legacy_routes(client, doc):
    document = client.get(f"/api/v1/documents/{doc}").json()
    fields = client.get(f"/api/v1/documents/{doc}/fields").json()
    amount = fields["loan_amount"]
    assert document["processing_run_id"] == amount["processing_run_id"]
    assert amount["document_id"] == doc and amount["field_name"] == "loan_amount"
    assert amount["verification_status"] == "verified"
    assert amount["evidence_status"] == "matched" and amount["evidence_ref"]
    assert amount["normalized_value"] == "500000"
    assert amount["source_text"] == amount["evidence"]
    assert all(clause["evidence_ref"] for clause in client.get(f"/api/v1/documents/{doc}/clauses").json())
def test_missing_stays_missing(client, tmp_path):
    import pymupdf; d = pymupdf.open(); d.new_page().insert_text((50, 80), "Loan Agreement\nGeneral notice text only, with nothing about money here."); d.save(tmp_path / "m.pdf")
    i = up(client, tmp_path / "m.pdf").json()["id"]
    assert client.get(f"/documents/{i}/terms").json()["loan_amount"]["value"] is None
def test_clauses_have_evidence(client, doc):
    cl = client.get(f"/documents/{doc}/clauses").json()
    assert any(c["category"] == "Prepayment and foreclosure" and c["page"] == 2 for c in cl) and all(c["evidence"] for c in cl)

# --- Q&A ---
def test_qa_supported(client, doc):
    r = client.post(f"/documents/{doc}/ask", json={"question": "What is the prepayment charge?"}).json()
    assert r["found"] and r["sources"][0]["page"] == 2 and "3.00%" in r["answer"]
def test_qa_unsupported(client, doc):
    r = client.post(f"/documents/{doc}/ask", json={"question": "What colour is the borrower's car?"}).json()
    assert not r["found"] and "Not found" in r["answer"]
def test_qa_injection_not_followed(client, tmp_path):
    make(tmp_path / "i.pdf", inject=True); i = up(client, tmp_path / "i.pdf").json()["id"]
    r = client.post(f"/documents/{i}/ask", json={"question": "What is the interest rate?"}).json()
    assert "no fees" not in r["answer"]

# --- EMI endpoint & comparison ---
def test_emi_endpoint(client, doc):
    r = client.post(f"/documents/{doc}/calculate-emi").json()
    assert r["calculated_emi"] == 16607.15 and "consistent" in r["note"]
def test_emi_endpoint_missing_inputs(client, doc):
    assert client.post(f"/documents/{doc}/calculate-emi", json={"months": 0}).status_code == 422
def test_compare(client, doc, tmp_path):
    make(tmp_path / "k.pdf", "kfs", rate=11.5, emi=16491); k = up(client, tmp_path / "k.pdf").json()["id"]
    response = client.post("/documents/compare", json={"document_id_a": doc, "document_id_b": k})
    result = response.json()
    assert result["comparison_id"]
    rows = {r["field"]: r["status"] for r in result["rows"]}
    assert rows["loan_amount"] == "match" and rows["interest_rate"] == "potential_difference" and rows["emi"] == "potential_difference"
    saved = client.get(f"/api/v1/comparisons/{result['comparison_id']}").json()
    assert saved["result"]["rows"] == result["rows"]
    assert client.get(f"/api/v1/comparisons/{result['comparison_id']}/report").content.startswith(b"%PDF-")

def test_field_correction_keeps_extraction_evidence_and_records_audit(client, doc):
    original = client.get(f"/api/v1/documents/{doc}/fields").json()["interest_rate"]
    response = client.patch(f"/api/v1/documents/{doc}/fields/interest_rate", json={
        "value": "11.5", "reason": "Confirmed against the signed offer page."
    })
    assert response.status_code == 200, response.text
    corrected = response.json()
    assert corrected["value"] == original["value"] == 12.0
    assert corrected["effective_value"] == 11.5
    assert corrected["raw"] == original["raw"]
    assert corrected["evidence"] == original["evidence"]
    assert corrected["user_correction"]["raw"] == "11.5"
    assert corrected["user_correction"]["actor_id"] == "test-owner"
    assert corrected["correction_history"][-1]["action"] == "set"
    assert corrected["correction_history"][-1]["reason"] == "Confirmed against the signed offer page."
    persisted = client.get(f"/api/v1/documents/{doc}/fields").json()["interest_rate"]
    assert persisted["effective_value"] == 11.5 and len(persisted["correction_history"]) == 1

def test_field_correction_is_used_by_emi_and_comparison(client, doc, tmp_path):
    make(tmp_path / "k.pdf", "kfs", rate=11.5, emi=16491)
    other = up(client, tmp_path / "k.pdf").json()["id"]
    client.patch(f"/api/v1/documents/{doc}/fields/interest_rate", json={"value": "11.5"})
    rows = {row["field"]: row for row in client.post("/api/v1/comparisons", json={
        "document_id_a": doc, "document_id_b": other,
    }).json()["rows"]}
    assert rows["interest_rate"]["status"] == "match"
    assert rows["interest_rate"]["a"]["manual"] is True
    assert "interest rate" in client.post(f"/api/v1/documents/{doc}/calculate-emi").json()["assumptions"][-1]

def test_field_correction_can_be_reverted_without_erasing_history(client, doc):
    path = f"/api/v1/documents/{doc}/fields/loan_amount"
    first = client.patch(path, json={"value": "600000", "reason": "Read the signed schedule."})
    assert first.status_code == 200
    reverted = client.patch(path, json={"value": None, "reason": "Rechecked source."})
    assert reverted.status_code == 200
    term = reverted.json()
    assert term["value"] == term["effective_value"] == 500000
    assert term["user_correction"] is None
    assert [event["action"] for event in term["correction_history"]] == ["set", "revert"]

@pytest.mark.parametrize("value", ["", "not a number", "-10", "₹0"])
def test_field_correction_rejects_invalid_amounts(client, doc, value):
    response = client.patch(f"/api/v1/documents/{doc}/fields/loan_amount", json={"value": value})
    assert response.status_code == 422

def test_fee_correction_preserves_percentage_units_and_rejects_bad_grouping(client, doc):
    path = f"/api/v1/documents/{doc}/fields/processing_fee"
    response = client.patch(path, json={"value": "2.50%"})
    assert response.status_code == 200
    assert response.json()["effective_value"] == "2.5%"
    assert response.json()["user_correction"]["raw"] == "2.50%"
    malformed = client.patch(path, json={"value": "1,2,3"})
    assert malformed.status_code == 422

def test_percentage_fee_corrections_compare_with_matching_units(client, doc, tmp_path):
    make(tmp_path / "k.pdf", "kfs", rate=12, emi=16607)
    other = up(client, tmp_path / "k.pdf").json()["id"]
    for document_id in (doc, other):
        response = client.patch(f"/api/v1/documents/{document_id}/fields/processing_fee", json={"value": "2.5%"})
        assert response.status_code == 200
    result = client.post("/api/v1/comparisons", json={"document_id_a": doc, "document_id_b": other}).json()
    fee = next(row for row in result["rows"] if row["field"] == "processing_fee")
    assert fee["status"] == "match"

def test_field_correction_is_owner_scoped(client, doc):
    from backend.app.main import app
    app.dependency_overrides[auth.get_current_user] = lambda: {"id": "other-owner", "email": "other@example.test"}
    response = client.patch(f"/api/v1/documents/{doc}/fields/loan_amount", json={"value": "600000"})
    assert response.status_code == 404

def test_document_report_marks_saved_correction_and_keeps_original_value(client, doc):
    client.patch(f"/api/v1/documents/{doc}/fields/loan_amount", json={"value": "600000"})
    report = client.get(f"/api/v1/documents/{doc}/report")
    assert report.status_code == 200
    assert "INR 500,000" in report.text
    assert "corrected to 600000" in report.text
def test_report_and_delete(client, doc):
    assert "BankEase AI report" in client.get(f"/documents/{doc}/report").text
    assert client.delete(f"/documents/{doc}").status_code == 204 and client.get(f"/documents/{doc}").status_code == 404
