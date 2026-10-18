import html, os, uuid, pathlib
import pymupdf
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import compare as cmp, extract, financial, qa, store

MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024
RETENTION_HOURS = float(os.getenv("BANKEASE_RETENTION_HOURS", "24"))  # 0 disables purge
DISCLAIMER = ("BankEase AI provides document explanation and information extraction for informational and educational purposes. "
              "It does not provide legal, financial or investment advice and does not determine whether a contractual term is legally valid. "
              "Users should consult an appropriately qualified professional when professional advice is required.")
app = FastAPI(title="BankEase AI", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8000").split(","), allow_methods=["*"], allow_headers=["*"])

class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=500)
class EmiIn(BaseModel):
    principal: float | None = None
    annual_rate: float | None = None
    months: int | None = None
class CompareIn(BaseModel):
    document_id_a: str
    document_id_b: str

def get(i):
    try: return store.load(i)
    except KeyError: raise HTTPException(404, "Document not found")

@app.get("/health")
def health():
    return {"status": "ok", "ai_mode": "deterministic-fallback", "persistence": "local-json"}

@app.post("/documents/analyze", status_code=201)
async def analyze(file: UploadFile = File(...)):
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES: raise HTTPException(413, "File too large")
    if not data.startswith(b"%PDF"): raise HTTPException(415, "Only PDF files are supported")
    store.purge(RETENTION_HOURS)
    did = uuid.uuid4().hex  # raw PDF is parsed from memory and never written to disk
    rec = {"id": did, "filename": os.path.basename(file.filename or "upload.pdf")[:120], "status": "processing"}
    try:
        with pymupdf.open(stream=data, filetype="pdf") as d:
            pages = [p.get_text() for p in d]
        empty = [i for i, t in enumerate(pages, 1) if len(t.strip()) < 20]
        if not pages or len(empty) == len(pages):
            raise ValueError("No selectable text found (scanned PDFs need OCR, which is not implemented).")
        dtype, cat = extract.classify(pages)
        rec.update(status="completed", pages=pages, page_count=len(pages), unreadable_pages=empty,
                   doc_type=dtype, loan_category=cat, terms=extract.extract_terms(pages), clauses=extract.extract_clauses(pages))
    except Exception as e:
        raise HTTPException(422, str(e)[:200])
    store.save(rec)
    return {k: rec[k] for k in ("id", "filename", "status", "doc_type", "loan_category", "page_count", "unreadable_pages")}

@app.get("/documents/{i}")
def doc(i: str):
    r = get(i); return {k: v for k, v in r.items() if k != "pages"}
@app.get("/documents/{i}/status")
def status(i: str):
    r = get(i); return {"id": i, "status": r["status"], "error": r.get("error")}
@app.get("/documents/{i}/terms")
def terms(i: str): return get(i).get("terms", {})
@app.get("/documents/{i}/clauses")
def clauses(i: str): return get(i).get("clauses", [])

@app.post("/documents/{i}/ask")
def ask(i: str, body: Ask):
    return qa.answer(body.question, get(i)["pages"])

@app.post("/documents/{i}/calculate-emi")
def calc(i: str, body: EmiIn | None = None):
    t, b = get(i)["terms"], body or EmiIn()
    p = b.principal if b.principal is not None else t["loan_amount"]["value"]
    r = b.annual_rate if b.annual_rate is not None else t["interest_rate"]["value"]
    n = b.months if b.months is not None else t["tenure"]["value"]
    try: return financial.summary(p, r, n, t["emi"]["value"])
    except financial.FinancialError as e: raise HTTPException(422, str(e))

@app.post("/documents/compare")
def compare(body: CompareIn):
    a, b = get(body.document_id_a), get(body.document_id_b)
    return {"a": a["filename"], "b": b["filename"], "rows": cmp.compare(a["terms"], b["terms"])}

@app.get("/documents/{i}/report", response_class=HTMLResponse)
def report(i: str):
    r, e = get(i), html.escape
    rows = "".join(f"<tr><td>{e(k)}</td><td>{e(str(v['raw']))}</td><td>{v['page'] or ''}</td><td>{e(v['status'])}</td></tr>" for k, v in r["terms"].items())
    cl = "".join(f"<li><b>{e(c['category'])}</b> (p.{c['page']}): {e(c['text'])}<br><i>Attention point: {e(c['attention_point'])}</i></li>" for c in r["clauses"])
    return (f"<html><body style='font-family:Georgia;max-width:800px;margin:auto'><h1>BankEase AI report</h1><p>{e(r['filename'])} — {e(r['doc_type'])}, {r['page_count']} page(s)</p>"
            f"<h2>Extracted terms</h2><table border=1 cellpadding=4><tr><th>Field</th><th>Value</th><th>Page</th><th>Status</th></tr>{rows}</table>"
            f"<h2>Clauses</h2><ul>{cl}</ul><h2>Limitations</h2><p>Rule-based extraction; verify against the original document.</p><p><small>{DISCLAIMER}</small></p></body></html>")

@app.delete("/documents/{i}", status_code=204)
def delete(i: str):
    get(i); store.delete(i)

app.mount("/", StaticFiles(directory=pathlib.Path(__file__).resolve().parents[1] / "static", html=True), name="ui")
