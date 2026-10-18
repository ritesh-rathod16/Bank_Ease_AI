"""Manifest-driven end-to-end tests over the synthetic dataset (expectations come from scripts/make_datasets.py)."""
import hashlib, json, os, pathlib, sys, time
import pymupdf, pytest
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from fastapi.testclient import TestClient
from backend.app import main, store
SAMPLES = ROOT / "data" / "samples"
MAN = json.loads((SAMPLES / "manifest.json").read_text())
FILES = MAN["files"]
OK = [f for f in FILES if f["expected_http"] == 201]
BAD = [f for f in FILES if f["expected_http"] != 201]
FIELDS = ["loan_amount", "interest_rate", "interest_type", "tenure", "emi", "processing_fee", "late_fee"]

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path / "rt")
    return TestClient(main.app)

def up(client, rel):
    p = SAMPLES / rel
    return client.post("/documents/analyze", files={"file": (p.name, p.read_bytes(), "application/pdf")})

_cache = {}
@pytest.fixture()
def ids(client):
    """Upload every valid sample once per test that needs them (cheap, ~20 docs)."""
    return {f["id"]: up(client, f["file"]).json()["id"] for f in OK}

def ident(f): return f["id"]

# ---------- dataset integrity ----------
def test_dataset_covers_all_five_categories_and_enough_files():
    cats = {f["category"] for f in FILES}
    assert {"personal", "home", "vehicle", "sanction_schedule", "comparison_edge"} <= cats
    assert len([f for f in FILES if f["file"].endswith(".pdf")]) >= 20
    assert {"personal", "home", "vehicle"} <= {f.get("loan_category") for f in FILES}
    assert (SAMPLES / "manifest.csv").exists()

@pytest.mark.parametrize("f", FILES, ids=ident)
def test_file_exists_and_checksum_matches(f):
    path = SAMPLES / f["file"]; assert path.exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == f["sha256"]

@pytest.mark.parametrize("f", [x for x in OK if x["scenario"] not in ("scanned_image_only",)], ids=ident)
def test_pdfs_have_page_numbers_and_page_count(f):
    with pymupdf.open(SAMPLES / f["file"]) as d:
        assert len(d) == f["page_count"]
        for i, pg in enumerate(d, 1):
            if i not in f["unreadable_pages"]: assert f"Page {i} of {len(d)}" in pg.get_text()

def test_synthetic_marker_and_no_real_identifiers():
    for f in OK:
        if f["scenario"] in ("partially_scanned",): continue
        with pymupdf.open(SAMPLES / f["file"]) as d: assert "SYNTHETIC" in d[0].get_text()

# ---------- upload / validation ----------
@pytest.mark.parametrize("f", OK, ids=ident)
def test_upload_and_classification(client, f):
    r = up(client, f["file"]); assert r.status_code == 201, r.text
    j = r.json(); assert j["doc_type"] == f["doc_type"] and j["loan_category"] == f["loan_category"]
    assert j["page_count"] == f["page_count"] and j["unreadable_pages"] == f["unreadable_pages"]

@pytest.mark.parametrize("f", BAD, ids=ident)
def test_rejected_files_leave_no_trace(client, f):
    r = up(client, f["file"]); assert r.status_code == f["expected_http"], r.text
    if f.get("expected_error"): assert f["expected_error"] in r.json()["detail"]
    rt = store.ROOT
    assert not list((rt / "records").glob("*")) if (rt / "records").exists() else True
    assert not list((rt / "uploads").glob("*")) if (rt / "uploads").exists() else True

def test_upload_size_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_BYTES", 1000)
    assert up(client, "personal/personal_loan_agreement_01.pdf").status_code == 413

def test_raw_pdf_is_never_written_to_disk(client):
    up(client, "personal/personal_loan_agreement_01.pdf")
    assert not list((store.ROOT / "uploads").glob("*")) if (store.ROOT / "uploads").exists() else True
    rec = next((store.ROOT / "records").glob("*.json")); assert oct(rec.stat().st_mode & 0o777) == "0o600"

# ---------- extraction + page citations ----------
@pytest.mark.parametrize("f", OK, ids=ident)
def test_terms_values_status_pages(client, f):
    i = up(client, f["file"]).json()["id"]; t = client.get(f"/documents/{i}/terms").json()
    for name in FIELDS:
        exp = f["expected_terms"][name]; got = t[name]
        assert got["status"] == exp["status"], (name, got, exp)
        if exp["status"] == "missing": assert got["value"] is None and got["evidence"] is None, (name, got)
        else:
            if isinstance(exp["value"], float): assert got["value"] == pytest.approx(exp["value"], abs=0.01), (name, got)
            elif exp["value"] is not None or got["value"] is not None: assert got["value"] == exp["value"], (name, got, exp)
            if exp["page"] is not None: assert got["page"] == exp["page"], (name, got, exp)
            assert got["evidence"], name

# ---------- clauses ----------
@pytest.mark.parametrize("f", [x for x in OK if x["expected_clauses"]], ids=ident)
def test_clause_categories_and_pages(client, f):
    i = up(client, f["file"]).json()["id"]; cl = client.get(f"/documents/{i}/clauses").json()
    for exp in f["expected_clauses"]:
        hit = [c for c in cl if c["text"].startswith(exp["starts"])]
        assert hit, f"clause not found: {exp}"
        assert hit[0]["category"] == exp["category"] and hit[0]["page"] == exp["page"], (exp, hit[0])
    assert all(c["evidence"] and c["page"] for c in cl)

# ---------- Q&A ----------
@pytest.mark.parametrize("f", [x for x in OK if x["expected_qa"]], ids=ident)
def test_qa_grounded_and_abstains(client, f):
    i = up(client, f["file"]).json()["id"]
    for q in f["expected_qa"]:
        r = client.post(f"/documents/{i}/ask", json={"question": q["question"]}).json()
        assert r["found"] == q["found"], (q, r)
        if q["found"]:
            assert q["contains"].lower() in r["answer"].lower(), (q, r)
            if "pages_include" in q: assert set(q["pages_include"]) <= {x["page"] for x in r["sources"]}, (q, r)
            else: assert r["sources"][0]["page"] == q["page"], (q, r)
        else:
            assert r["sources"] == [] and "Not found" in r["answer"]

def test_partially_scanned_page_is_not_answered(client):
    f = next(x for x in OK if x["scenario"] == "partially_scanned")
    i = up(client, f["file"]).json()["id"]
    assert {c["page"] for c in client.get(f"/documents/{i}/clauses").json()} == {1}   # nothing is read from the image page
    r = client.post(f"/documents/{i}/ask", json={"question": "What is the prepayment charge?"}).json()
    assert not r["found"]

# ---------- EMI ----------
@pytest.mark.parametrize("f", OK, ids=ident)
def test_emi_endpoint(client, f):
    i = up(client, f["file"]).json()["id"]; r = client.post(f"/documents/{i}/calculate-emi"); exp = f["expected_emi"]
    assert r.status_code == exp["http"], r.text
    if exp["http"] == 200:
        j = r.json(); assert j["calculated_emi"] == pytest.approx(exp["calculated"], abs=0.01)
        if exp["stated"] is not None:
            assert j["stated_emi"] == pytest.approx(exp["stated"], abs=0.01)
            assert j["note"].startswith("Stated installment is consistent") == exp["consistent"], j
            assert j["note"].startswith("Potential difference") != exp["consistent"]
            assert "error" not in j["note"].lower() and "fraud" not in j["note"].lower()

def test_emi_mismatch_sample_reports_positive_difference(client):
    f = next(x for x in OK if x["scenario"] == "emi_mismatch"); i = up(client, f["file"]).json()["id"]
    j = client.post(f"/documents/{i}/calculate-emi").json(); assert j["difference"] > 1000

def test_repayment_schedule_table_is_internally_consistent():
    f = next(x for x in OK if x["category"] == "sanction_schedule" and x["scenario"] == "amortization_table")
    assert f["schedule_rows"] == 36 and f["schedule_final_row"][-1] == "0.00"

# ---------- comparison ----------
@pytest.mark.parametrize("c", MAN["comparisons"], ids=lambda c: f"{c['a']}-vs-{c['b']}")
def test_comparisons(client, ids, c):
    rows = {r["field"]: r for r in client.post("/documents/compare", json={"document_id_a": ids[c["a"]], "document_id_b": ids[c["b"]]}).json()["rows"]}
    for field, status in c["expected"].items(): assert rows[field]["status"] == status, (c["purpose"], field, rows[field])
    if c["expected"].get("interest_rate") == "potential_difference":
        assert rows["interest_rate"]["a"]["page"] == 1 and rows["interest_rate"]["b"]["page"] == 1 and rows["interest_rate"]["a"]["value"] != rows["interest_rate"]["b"]["value"]

# ---------- prompt injection ----------
def test_prompt_injection_is_inert(client):
    f = next(x for x in OK if x["scenario"] == "prompt_injection"); i = up(client, f["file"]).json()["id"]
    t = client.get(f"/documents/{i}/terms").json()
    assert t["processing_fee"]["raw"] == "1.50%" and t["interest_rate"]["value"] == 13.5   # injected claims did not change facts
    r = client.post(f"/documents/{i}/ask", json={"question": "What is the processing fee?"}).json()
    assert any("Processing Fee: 1.50%" in x["text"] for x in r["sources"]) and r["answer"].startswith("The document states:")
    assert client.get("/health").json()["status"] == "ok"
    assert "admin" not in json.dumps(client.get(f"/documents/{i}").json())

# ---------- deletion + persistence ----------
def test_delete_removes_all_local_data(client):
    i = up(client, "home/home_loan_agreement_01.pdf").json()["id"]
    assert client.delete(f"/documents/{i}").status_code == 204
    assert client.get(f"/documents/{i}").status_code == 404 and client.post(f"/documents/{i}/ask", json={"question": "interest rate?"}).status_code == 404
    assert not list((store.ROOT / "records").glob("*")) and client.delete(f"/documents/{i}").status_code == 404

def test_retention_purge_removes_only_expired(client):
    old = up(client, "personal/personal_loan_agreement_01.pdf").json()["id"]
    p = store.ROOT / "records" / f"{old}.json"; t = time.time() - 48 * 3600; os.utime(p, (t, t))
    new = up(client, "personal/personal_loan_agreement_02_floating.pdf").json()["id"]   # analyze() triggers purge (24h default)
    assert client.get(f"/documents/{old}").status_code == 404 and client.get(f"/documents/{new}").status_code == 200
    assert store.purge(0) == 0

def test_path_traversal_ids_rejected(client):
    for bad in ["..%2F..%2Fetc%2Fpasswd", "not-a-hex-id", "A" * 32]: assert client.get(f"/documents/{bad}").status_code in (404, 422)

# ---------- regeneration is reproducible ----------
def test_generator_regenerates_same_expectations(tmp_path):
    import make_datasets
    m2 = make_datasets.build(tmp_path)
    strip = lambda m: [{k: v for k, v in f.items() if k not in ("sha256", "bytes")} for f in m["files"]]
    assert strip(m2) == strip(MAN) and m2["comparisons"] == MAN["comparisons"]
