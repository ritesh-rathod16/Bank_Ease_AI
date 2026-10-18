import sys, pathlib, pytest
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from fastapi.testclient import TestClient
from backend.app import financial, store
from scripts.make_synthetic import make

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    from backend.app.main import app
    return TestClient(app)

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
def test_bad_id_404(client): assert client.get("/documents/../../etc/passwd").status_code in (404, 422)
def test_extraction_and_pages(client, doc):
    t = client.get(f"/documents/{doc}/terms").json()
    assert (t["loan_amount"]["value"], t["interest_rate"]["value"], t["tenure"]["value"], t["emi"]["value"]) == (500000, 12.0, 36, 16607)
    assert t["loan_amount"]["page"] == 1 and "Loan Amount" in t["loan_amount"]["evidence"]
    assert t["interest_type"]["value"] == "fixed"
    assert client.get(f"/documents/{doc}").json()["doc_type"] == "loan_agreement"
def test_missing_stays_missing(client, tmp_path):
    import pymupdf; d = pymupdf.open(); d.new_page().insert_text((50, 80), "General notice text only, with nothing about money here."); d.save(tmp_path / "m.pdf")
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
    rows = {r["field"]: r["status"] for r in client.post("/documents/compare", json={"document_id_a": doc, "document_id_b": k}).json()["rows"]}
    assert rows["loan_amount"] == "match" and rows["interest_rate"] == "potential_difference" and rows["emi"] == "potential_difference"
def test_report_and_delete(client, doc):
    assert "BankEase AI report" in client.get(f"/documents/{doc}/report").text
    assert client.delete(f"/documents/{doc}").status_code == 204 and client.get(f"/documents/{doc}").status_code == 404
