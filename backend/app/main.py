import copy, html, os, uuid, re, math, threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
import pymupdf
from pymongo.errors import DuplicateKeyError
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Path as APIPath, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from . import auth, compare as cmp, conversations as conversation_routes, document_processing, extract, financial, financial_statements, ocr, qa, retention, reviews, statement_compare, statements, store
from .config import load_settings
from .database import connect as connect_mongodb

MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "100"))
DISCLAIMER = ("BankEase AI provides document explanation and information extraction for informational and educational purposes. "
              "It does not provide legal, financial or investment advice and does not determine whether a contractual term is legally valid. "
              "Users should consult an appropriately qualified professional when professional advice is required.")
@asynccontextmanager
async def lifespan(application):
    settings = load_settings()
    application.state.settings = settings
    client = None
    worker_stop = threading.Event()
    worker_thread = None
    application.state.document_worker_running = False
    if settings.storage_backend == "mongodb":
        client, db = connect_mongodb(settings.mongodb_uri, settings.mongodb_database,
                                     settings.mongo_connect_timeout_ms)
        store.configure_mongo(db)
        application.state.mongo_client = client
        application.state.mongo_database = db
    worker_enabled = os.getenv("BANKEASE_ENABLE_DOCUMENT_WORKER", "true").strip().lower() in {"1", "true", "yes", "on"}
    if settings.environment != "test" and worker_enabled:
        from . import jobs
        from .auth_store import AuthRepository
        repository = AuthRepository(db=getattr(application.state, "mongo_database", None), root=store.ROOT)
        worker_thread = threading.Thread(target=jobs.run_worker,
                                         args=(repository, worker_stop, 0.5, settings.retention_hours,
                                               settings.retention_sweep_seconds),
                                         name="bankease-document-worker", daemon=True)
        application.state.document_worker_thread = worker_thread
        worker_thread.start()
        application.state.document_worker_running = True
    try:
        yield
    finally:
        worker_stop.set()
        if worker_thread is not None:
            worker_thread.join(timeout=5)
        store.configure_mongo(None)
        if client is not None:
            client.close()

app = FastAPI(title="BankEase AI", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8000").split(","), allow_methods=["*"], allow_headers=["*"])
app.include_router(auth.router)
app.include_router(reviews.router)
app.include_router(conversation_routes.router)
app.include_router(conversation_routes.ai_router)

class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=500)
class EmiIn(BaseModel):
    principal: float | None = None
    annual_rate: float | None = None
    months: int | None = None
class CompareIn(BaseModel):
    document_id_a: str
    document_id_b: str
    overrides_a: dict[str, str | None] | None = None
    overrides_b: dict[str, str | None] | None = None
class TermCorrectionIn(BaseModel):
    value: str | None
    reason: str = Field(default="", max_length=500)
class TransactionCorrectionIn(BaseModel):
    date: str | None = None
    description: str | None = None
    amount_minor: int | None = Field(default=None, strict=True)
    balance_minor: int | None = Field(default=None, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    reason: str = Field(default="", max_length=500)

def get(i, owner_id):
    try: return store.load(i, owner_id)
    except KeyError: raise HTTPException(404, "Document not found")

def _confirmed_upcoming_obligations(records, owner_id, today=None):
    """Only include future source dates explicitly confirmed by the document owner."""
    today = today or datetime.now(timezone.utc).date()
    obligation_fields = {
        "first_emi_date": "First loan installment",
        "loan_maturity_date": "Loan maturity",
        "interest_reset_date": "Interest rate reset",
        "policy_expiry_date": "Insurance policy renewal",
    }
    obligations = []
    for record in records:
        if record.get("status") not in {"completed", "needs_review"} or str(record.get("doc_type", "")).startswith("bank_statement_"):
            continue
        terms = record.get("terms", {})
        for field, title in obligation_fields.items():
            term = terms.get(field) or {}
            correction = term.get("user_correction")
            if not correction or correction.get("actor_id") != owner_id or not term.get("page") or not term.get("evidence"):
                continue
            value = term.get("effective_value", correction.get("value"))
            try:
                due_date = date.fromisoformat(value)
            except (TypeError, ValueError):
                continue
            if due_date < today or due_date.isoformat() != value:
                continue
            obligation = {
                "document_id": record["id"], "filename": record.get("filename", "Document"),
                "due_date": due_date.isoformat(), "title": title,
                "date_page": term["page"], "date_evidence": term["evidence"],
                "confirmed_by_owner": True,
            }
            if term.get("note"):
                obligation["date_note"] = term["note"]
            emi = terms.get("emi") or {}
            emi_value = emi.get("effective_value", emi.get("value"))
            emi_is_supported = emi.get("status") == "extracted" or bool(emi.get("user_correction"))
            if field == "first_emi_date" and emi_is_supported and isinstance(emi_value, (int, float)) and not isinstance(emi_value, bool) and emi_value > 0:
                obligation.update(amount=emi_value, amount_label="Stated EMI",
                                  amount_page=emi.get("page"), amount_evidence=emi.get("evidence"))
            obligations.append(obligation)
    obligations.sort(key=lambda item: (item["due_date"], item["filename"], item["title"]))
    return obligations

def _persist_comparison(owner_id, kind, document_ids, title, request, result):
    saved_result = copy.deepcopy(result)
    truncated = False
    if kind == "statement" and len(saved_result.get("differences", [])) > 500:
        saved_result["differences"] = saved_result["differences"][:500]
        saved_result["history_truncated"] = True
        truncated = True
    summary = copy.deepcopy(saved_result.get("summary", {}))
    if kind == "loan":
        summary["row_count"] = len(saved_result.get("rows", []))
        summary["difference_count"] = sum(
            row.get("status") != "match"
            for row in saved_result.get("rows", [])
        )
    record = {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "title": title,
        "document_ids": list(document_ids),
        "request": request,
        "summary": summary,
        "history_truncated": truncated,
        "result": saved_result,
    }
    try:
        return store.save_comparison(record, owner_id)
    except KeyError as exc:
        raise HTTPException(404, "A source document is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

def _corrected_value(field, entered):
    if entered is None:
        return None
    raw = entered.strip()
    if not raw:
        raise HTTPException(422, "Enter a value or send null to revert to the extracted value.")
    if len(raw) > 120:
        raise HTTPException(422, "Correction values must be 120 characters or fewer.")
    if field in extract.CORRECTABLE_DATE_FIELDS:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            raise HTTPException(422, "Enter the confirmed date as YYYY-MM-DD.")
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError as exc:
            raise HTTPException(422, "Enter the confirmed date as YYYY-MM-DD.") from exc
    if field == "interest_type":
        value = raw.casefold()
        if value not in {"fixed", "floating"}:
            raise HTTPException(422, "Interest type must be fixed or floating.")
        return value
    if field in extract.INSURANCE_TEXT_FIELDS | extract.KYC_TEXT_FIELDS | extract.AUDIT_TEXT_FIELDS:
        if any(ord(character) < 32 for character in raw):
            raise HTTPException(422, "Text corrections cannot include control characters.")
        return re.sub(r"\s+", " ", raw)
    if field in extract.FINANCIAL_AMOUNT_FIELDS:
        match = re.fullmatch(r"\s*(?:INR|Rs\.?|â‚¹)?\s*(\(?-?\d[\d,]*(?:\.\d+)?\)?)\s*", raw, re.I)
        if not match:
            raise HTTPException(422, "Enter one financial statement amount; negative values are supported.")
        number_raw = match.group(1)
        number = number_raw.strip("()")
        integer_group = number.lstrip("-").split(".", 1)[0]
        if "," in integer_group:
            groups = integer_group.split(",")
            if not (1 <= len(groups[0]) <= 3 and len(groups[-1]) == 3
                    and all(len(group) in {2, 3} for group in groups[1:-1])):
                raise HTTPException(422, "Check the digit grouping in this amount.")
        try:
            decimal = Decimal(number.replace(",", ""))
        except InvalidOperation as exc:
            raise HTTPException(422, "Enter a valid financial statement amount.") from exc
        if number_raw.startswith("(") and number_raw.endswith(")"):
            decimal = -abs(decimal)
        if not decimal.is_finite() or abs(decimal) > Decimal("1000000000000000000"):
            raise HTTPException(422, "Amount is outside the supported range.")
        return format(decimal.normalize(), "f") if decimal else "0"
    if field == "tenure":
        match = re.fullmatch(r"\s*(\d+)\s*(months?|years?)?\s*", raw, re.I)
        if not match:
            raise HTTPException(422, "Enter tenure as whole months or years.")
        value = int(match.group(1)) * (12 if (match.group(2) or "").lower().startswith("year") else 1)
        if value <= 0 or value > 1200:
            raise HTTPException(422, "Tenure must be between 1 and 1,200 months.")
        return value
    match = re.fullmatch(r"\s*(?:(?:INR|Rs\.?|₹)\s*)?(\d[\d,]*(?:\.\d+)?)\s*(%)?\s*", raw, re.I)
    if not match:
        raise HTTPException(422, "Enter a non-negative numeric value, with % for percentage fees.")
    integer_group = match.group(1).split(".", 1)[0]
    if "," in integer_group:
        groups = integer_group.split(",")
        if not (1 <= len(groups[0]) <= 3 and len(groups[-1]) == 3
                and all(len(group) in {2, 3} for group in groups[1:-1])):
            raise HTTPException(422, "Check the digit grouping in this value.")
    try:
        number = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation as exc:
        raise HTTPException(422, "Enter a valid numeric value.") from exc
    if not number.is_finite() or number < 0 or number > Decimal("1000000000000"):
        raise HTTPException(422, "Enter a finite non-negative value within the supported range.")
    percentage = bool(match.group(2))
    if percentage and number > Decimal("1000"):
        raise HTTPException(422, "Percentage corrections cannot exceed 1,000%.")
    if field in {"loan_amount", "emi", *extract.INSURANCE_MONEY_FIELDS} and (number <= 0 or percentage):
        raise HTTPException(422, f"{field.replace('_', ' ').capitalize()} must be a positive amount.")
    if field == "interest_rate" and percentage:
        return float(number)
    if field in {"processing_fee", "late_fee"} and percentage:
        return f"{format(number.normalize(), 'f')}%"
    if percentage:
        raise HTTPException(422, "A percentage sign is only supported for interest and fee fields.")
    return float(number)

def effective_terms(terms):
    """Copy terms with saved corrections applied while retaining source evidence."""
    result = {name: dict(term) for name, term in terms.items()}
    for term in result.values():
        correction = term.get("user_correction")
        if correction is not None:
            term["value"] = term.get("effective_value", correction.get("value"))
            term["raw"] = correction.get("raw")
            term["status"] = "manual"
            term["manual_source"] = "saved_correction"
            term["note"] = "Saved user correction; original source evidence remains unchanged."
    return result

@app.get("/health")
def health(request: Request):
    return {"status": "ok", "ai_mode": "deterministic-fallback", "persistence": store.backend_name(),
            "document_worker": "running" if getattr(request.app.state, "document_worker_thread", None)
            and request.app.state.document_worker_thread.is_alive() else "disabled"}

@app.get("/documents")
@app.get("/api/v1/documents")
def list_documents(user=Depends(auth.get_current_user)):
    records = store.list_owner(user["id"])
    records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    fields = ("id", "filename", "status", "error", "analysis_note", "doc_type", "suggested_doc_type", "suggested_loan_category", "loan_category", "page_count", "ocr_pages", "transaction_count", "currency", "reconciliation_status", "version_group_id", "version_number", "previous_version_id", "created_at", "updated_at")
    return {"documents": [{key: item.get(key) for key in fields} for item in records]}

@app.get("/api/v1/dashboard/summary")
def dashboard_summary(request: Request, user=Depends(auth.get_current_user)):
    records = store.list_owner(user["id"])
    records.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    categories = {}
    term_reviews = 0
    statement_signals = 0
    financial_signals = 0
    audit_signals = 0
    finding_count = 0
    home_loan = None
    obligations = _confirmed_upcoming_obligations(records, user["id"])
    for record in records:
        if record.get("doc_type") != "audit_report":
            for clause in record.get("clauses", []):
                category = clause.get("category", "Other")
                categories[category] = categories.get(category, 0) + 1
                finding_count += 1
        else:
            audit_signals += 1
        term_reviews += sum(term.get("status") in {"review", "ambiguous", "conflict"}
                            for term in record.get("terms", {}).values())
        analysis = record.get("statement_analysis", {})
        statement_signals += len(analysis.get("balance_issues", [])) + len(analysis.get("duplicate_candidates", []))
        if record.get("doc_type") == "financial_statement":
            for check in financial_statements.verify_totals(record.get("terms", {})):
                if check.get("status") == "difference":
                    categories["Financial statement arithmetic"] = categories.get("Financial statement arithmetic", 0) + 1
                    finding_count += 1
                    financial_signals += 1
                elif check.get("status") == "not_evaluated":
                    financial_signals += 1
        if home_loan is None and record.get("loan_category") == "home" and not str(record.get("doc_type", "")).startswith("bank_statement_"):
            home_loan = record["id"]
    repo = auth._repo(request)
    if user.get("role") == "system_admin":
        tasks = repo.find_many("review_tasks")
    elif user.get("role") == "customer":
        tasks = repo.find_many("review_tasks", {"owner_id": user["id"]})
    else:
        tasks = repo.find_many("review_tasks", {"assigned_to": user["id"]})
    open_reviews = sum(task.get("status") not in {"approved", "rejected"} for task in tasks)
    summary_fields = ("id", "filename", "status", "error", "analysis_note", "doc_type", "suggested_doc_type", "suggested_loan_category", "source_format", "source_locator_type", "section_count", "loan_category", "page_count", "transaction_count", "currency", "reconciliation_status", "created_at", "updated_at")
    docs = [{field: item.get(field) for field in summary_fields} for item in records]
    return {"documents": docs, "documents_analyzed": sum(item.get("status") == "completed" for item in records),
            "review_count": open_reviews + term_reviews + statement_signals + financial_signals + audit_signals, "open_review_tasks": open_reviews,
            "term_review_count": term_reviews, "statement_signals": statement_signals,
            "financial_signals": financial_signals, "audit_signals": audit_signals,
            "finding_count": finding_count, "findings_by_category": categories,
            "home_loan_document_id": home_loan, "upcoming_obligations": obligations,
            "obligation_note": "Only future dates you confirm against source evidence are listed. Recurring payments are not inferred."}

@app.post("/documents/analyze", status_code=201, dependencies=[Depends(auth.require_csrf)])
@app.post("/api/v1/documents", status_code=201, dependencies=[Depends(auth.require_csrf)])
async def analyze(request: Request, response: Response, file: UploadFile = File(...), supersedes_document_id: str | None = Form(None),
                  user=Depends(auth.get_current_user)):
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES: raise HTTPException(413, "File too large")
    filename = os.path.basename(file.filename or "upload")[:120]
    upload_format = document_processing.detect_upload_format(data, filename)
    did = uuid.uuid4().hex
    configured_async = os.getenv("BANKEASE_ASYNC_DOCUMENTS")
    settings = load_settings()
    async_mode = (configured_async.strip().lower() in {"1", "true", "yes", "on"}) if configured_async is not None else settings.environment != "test"
    repository = auth._repo(request)
    retention.sweep_expired(repository, settings.retention_hours, owner_id=user["id"])
    if async_mode:
        rec = {
            "id": did, "filename": filename, "status": "processing", "doc_type": "pending",
            "loan_category": None, "page_count": 0, "unreadable_pages": [], "ocr_pages": [],
            "terms": {}, "clauses": [], "pages": [], "processing_job_id": did,
            "upload_format": upload_format,
        }
    else:
        rec = document_processing.analyze_document_bytes(data, filename, did, max_pdf_pages=MAX_PDF_PAGES)
    if supersedes_document_id:
        parent = get(supersedes_document_id, user["id"])
        parent = store.ensure_version_metadata(parent["id"], user["id"])
        family = store.version_history(parent["id"], user["id"])
        latest = family["versions"][-1]
        if latest["id"] != parent["id"]:
            raise HTTPException(409, "Only the latest document version can be superseded.")
        same_statement_family = (
            str(parent.get("doc_type", "")).startswith("bank_statement_")
            and str(rec.get("doc_type", "")).startswith("bank_statement_")
        )
        same_document_type = (
            parent.get("doc_type") == rec.get("doc_type")
            and parent.get("loan_category") == rec.get("loan_category")
        )
        if not async_mode and not (same_document_type or same_statement_family):
            raise HTTPException(409, "A revision must use the same document type or remain within the bank-statement family.")
        rec.update(
            version_group_id=family["version_group_id"],
            version_number=parent.get("version_number", latest["version_number"]) + 1,
            previous_version_id=parent["id"],
        )
        if async_mode:
            rec["supersedes_document_id"] = parent["id"]
    else:
        rec.update(version_group_id=did, version_number=1, previous_version_id=None)
    now = datetime.now(timezone.utc).isoformat()
    rec.setdefault("created_at", now)
    rec["updated_at"] = now
    extension = upload_format
    source_file = store.save_source_file(did, extension, data)
    rec["source_file"] = source_file
    try:
        store.save(rec, user["id"])
        if async_mode:
            from . import jobs
            jobs.enqueue(repository, user["id"], did)
    except DuplicateKeyError as exc:
        try:
            store.delete(did, user["id"])
        except KeyError:
            pass
        store.remove_source_file(did, source_file)
        raise HTTPException(409, "A revision was added concurrently. Reload the version history and try again.") from exc
    except Exception:
        store.remove_source_file(did, source_file)
        try:
            store.delete(did, user["id"])
        except KeyError:
            pass
        raise
    keys = ("id", "filename", "status", "analysis_note", "doc_type", "suggested_doc_type", "suggested_loan_category", "source_format", "source_locator_type", "section_count", "loan_category", "page_count", "unreadable_pages", "ocr_pages",
            "transaction_count", "currency", "reconciliation_status", "version_group_id", "version_number", "previous_version_id")
    if async_mode:
        response.status_code = 202
    return {key: rec[key] for key in keys if key in rec}

@app.get("/api/v1/search")
def search_documents(q: str = Query(min_length=2, max_length=120), limit: int = Query(20, ge=1, le=50),
                     user=Depends(auth.get_current_user)):
    """Search only the current owner's uploaded content and extracted evidence."""
    needle = " ".join(q.casefold().split())
    results = []

    def add(record, kind, text, page=None, location=None):
        if len(results) >= limit or not text or needle not in " ".join(str(text).casefold().split()):
            return
        excerpt = " ".join(str(text).split())
        results.append({"document_id": record["id"], "filename": record.get("filename", "Document"),
                        "doc_type": record.get("doc_type", "unknown"), "kind": kind, "page": page, "location": location,
                        "excerpt": excerpt[:600]})

    records = sorted(store.list_owner(user["id"]), key=lambda item: item.get("created_at", ""), reverse=True)
    for summary in records:
        if len(results) >= limit:
            break
        try:
            record = store.load(summary["id"], user["id"])
        except KeyError:
            continue
        add(record, "document", f"{record.get('filename', '')} {record.get('doc_type', '')}")
        is_kyc_form = record.get("doc_type") == "kyc_form"
        if not is_kyc_form:
            for name, term in record.get("terms", {}).items():
                add(record, "field", f"{name}: {term.get('raw') or term.get('effective_value') or term.get('value') or ''}. {term.get('evidence') or ''}", term.get("page"))
        for clause in record.get("clauses", []):
            add(record, "clause", f"{clause.get('category', '')}: {clause.get('text', '')}", clause.get("page"))
        if record.get("doc_type") == "financial_statement":
            for check in financial_statements.verify_totals(record.get("terms", {})):
                if check.get("status") == "difference":
                    add(record, "finding", check.get("summary", "Financial statement arithmetic difference"),
                        (check.get("sources") or [{}])[0].get("page"))
        if not is_kyc_form:
            for page_number, text in enumerate(record.get("pages", []), 1):
                for line in text.splitlines():
                    if record.get("source_locator_type") == "section":
                        add(record, "section", line, location=f"extracted text section {page_number}")
                    else:
                        add(record, "page", line, page_number)
                    if len(results) >= limit:
                        break
        if str(record.get("doc_type", "")).startswith("bank_statement_"):
            for row in store.all_transactions(record["id"], user["id"]):
                effective = row.get("effective") or row
                add(record, "transaction", f"{effective.get('date', '')} {effective.get('description', '')} {effective.get('amount_minor', '')} {effective.get('balance_minor', '')} {row.get('evidence', '')}", row.get("page"))
                if len(results) >= limit:
                    break
    return {"query": q, "results": results, "limit": limit}

@app.get("/documents/{i}")
@app.get("/api/v1/documents/{i}")
def doc(i: str, user=Depends(auth.get_current_user)):
    r = get(i, user["id"])
    output = {**{k: v for k, v in r.items() if k not in ("pages", "transactions", "source_file")},
              "source_available": bool(r.get("source_file"))}
    if r.get("doc_type") == "financial_statement":
        output["financial_checks"] = financial_statements.verify_totals(r.get("terms", {}))
    return output

@app.get("/api/v1/documents/{i}/versions")
def document_versions(i: str, user=Depends(auth.get_current_user)):
    try:
        return store.version_history(i, user["id"])
    except KeyError as exc:
        raise HTTPException(404, "Document not found") from exc

@app.get("/documents/{i}/preview")
@app.get("/api/v1/documents/{i}/preview")
def preview(i: str, user=Depends(auth.get_current_user)):
    try:
        path, media_type = store.source_path(i, user["id"])
    except KeyError as exc:
        raise HTTPException(404, "Document not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Original source file is unavailable") from exc
    return FileResponse(path, media_type=media_type, headers={
        "Content-Disposition": "inline" if media_type == "application/pdf" or media_type.startswith("image/") else "attachment",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Cross-Origin-Resource-Policy": "same-origin",
    })

@app.get("/documents/{i}/transactions")
@app.get("/api/v1/documents/{i}/transactions")
def document_transactions(i: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                          search: str = Query("", max_length=100), date_from: date | None = None,
                          date_to: date | None = None, user=Depends(auth.get_current_user)):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "The start date must be on or before the end date.")
    try:
        return store.transaction_page(
            i, user["id"], offset, limit, search, date_from.isoformat() if date_from else None,
            date_to.isoformat() if date_to else None,
        )
    except KeyError as exc:
        raise HTTPException(404, "Document not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.patch("/api/v1/documents/{i}/transactions/{row_number}", dependencies=[Depends(auth.require_csrf)])
def correct_transaction(i: str, row_number: int = APIPath(ge=1), body: TransactionCorrectionIn = Body(...),
                        user=Depends(auth.get_current_user)):
    changes = {field: getattr(body, field) for field in
               ("date", "description", "amount_minor", "balance_minor")
               if field in body.model_fields_set}
    if not changes:
        raise HTTPException(422, "Provide at least one transaction field to correct.")
    if "date" in changes and changes["date"] is not None:
        value = changes["date"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise HTTPException(422, "Corrected transaction dates must use YYYY-MM-DD.")
        try:
            changes["date"] = date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise HTTPException(422, "Enter a valid transaction date.") from exc
    if "description" in changes and changes["description"] is not None:
        value = changes["description"].strip()
        if not value or len(value) > 500 or any(ord(char) < 32 and char not in "\t" for char in value):
            raise HTTPException(422, "Description must contain 1 to 500 printable characters.")
        changes["description"] = value
    from .statements import MAX_MINOR_UNITS
    for field in ("amount_minor", "balance_minor"):
        value = changes.get(field)
        if value is not None and (abs(value) > MAX_MINOR_UNITS or field == "amount_minor" and value == 0):
            raise HTTPException(422, "Transaction amounts must be non-zero and within the supported exact numeric range; balances must be within that range.")
    try:
        return store.record_transaction_correction(
            i, user["id"], row_number, changes, body.reason.strip() or None,
            user["id"], body.expected_revision,
        )
    except KeyError as exc:
        raise HTTPException(404, "Statement transaction not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.get("/documents/{i}/status")
@app.get("/api/v1/documents/{i}/status")
def status(i: str, request: Request, user=Depends(auth.get_current_user)):
    record = get(i, user["id"])
    job = auth._repo(request).find_one("processing_jobs", {"_id": i, "owner_id": user["id"]})
    from .jobs import public_job
    return {"id": i, "status": record["status"], "error": record.get("error"), "job": public_job(job)}


@app.post("/api/v1/documents/{i}/retry", status_code=202, dependencies=[Depends(auth.require_csrf)])
def retry_document(i: str, request: Request, user=Depends(auth.get_current_user)):
    record = get(i, user["id"])
    repository = auth._repo(request)
    job = repository.find_one("processing_jobs", {"_id": i, "owner_id": user["id"]})
    if not job or job.get("status") != "failed":
        raise HTTPException(409, "Only a failed background analysis can be retried.")
    from .jobs import public_job, retry
    updated = retry(repository, user["id"], i)
    record.update(status="processing", error=None, updated_at=datetime.now(timezone.utc).isoformat())
    store.save(record, user["id"])
    return {"id": i, "status": "processing", "job": public_job(updated)}
@app.get("/documents/{i}/terms")
@app.get("/api/v1/documents/{i}/terms")
@app.get("/api/v1/documents/{i}/fields")
def terms(i: str, user=Depends(auth.get_current_user)): return get(i, user["id"]).get("terms", {})
@app.patch("/api/v1/documents/{i}/fields/{field_id}", dependencies=[Depends(auth.require_csrf)])
def correct_term(i: str, field_id: str, body: TermCorrectionIn, user=Depends(auth.get_current_user)):
    if field_id not in extract.CORRECTABLE_FIELDS:
        raise HTTPException(404, "Extracted field not found")
    record = get(i, user["id"])
    if str(record.get("doc_type", "")).startswith("bank_statement_"):
        raise HTTPException(409, "Statement rows cannot be corrected through the loan-term editor.")
    if record.get("status") not in {"completed", "needs_review"}:
        raise HTTPException(409, "Document fields can only be corrected after processing has finished.")
    if field_id not in record.get("terms", {}):
        raise HTTPException(404, "Extracted field not found")
    value = _corrected_value(field_id, body.value)
    raw = body.value.strip() if body.value is not None else None
    try:
        return store.record_term_correction(
            i, user["id"], field_id, value, raw, body.reason.strip() or None, user["id"]
        )
    except KeyError as exc:
        raise HTTPException(404, "Document or extracted field not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
@app.get("/documents/{i}/clauses")
@app.get("/api/v1/documents/{i}/clauses")
def clauses(i: str, user=Depends(auth.get_current_user)): return get(i, user["id"]).get("clauses", [])

@app.post("/documents/{i}/ask", dependencies=[Depends(auth.require_csrf)])
@app.post("/api/v1/documents/{i}/ask", dependencies=[Depends(auth.require_csrf)])
def ask(i: str, body: Ask, user=Depends(auth.get_current_user)):
    record = get(i, user["id"])
    if str(record.get("doc_type", "")).startswith("bank_statement_") or record.get("doc_type") == "kyc_form" or not record.get("pages"):
        raise HTTPException(409, "Document questions are not available for this document type yet.")
    if record.get("source_locator_type") == "section":
        return qa.answer_sections(body.question, record["pages"])
    return qa.answer(body.question, record["pages"])

@app.post("/documents/{i}/calculate-emi", dependencies=[Depends(auth.require_csrf)])
@app.post("/api/v1/documents/{i}/calculate-emi", dependencies=[Depends(auth.require_csrf)])
def calc(i: str, body: EmiIn | None = None, user=Depends(auth.get_current_user)):
    record = get(i, user["id"])
    if record.get("doc_type") not in extract.LOAN_DOCUMENT_TYPES:
        raise HTTPException(409, "Loan repayment calculations are only available for supported loan documents.")
    t, b = effective_terms(record["terms"]), body or EmiIn()
    p = b.principal if b.principal is not None else t["loan_amount"]["value"]
    r = b.annual_rate if b.annual_rate is not None else t["interest_rate"]["value"]
    n = b.months if b.months is not None else t["tenure"]["value"]
    try:
        result = financial.summary(p, r, n, t["emi"]["value"])
        corrected = [name.replace("_", " ") for name, term in t.items()
                     if name in {"loan_amount", "interest_rate", "tenure", "emi"}
                     and term.get("status") == "manual"]
        if corrected:
            result["assumptions"].append("Saved user corrections used for: " + ", ".join(corrected))
        return result
    except financial.FinancialError as e: raise HTTPException(422, str(e))

@app.post("/documents/compare", dependencies=[Depends(auth.require_csrf)])
@app.post("/api/v1/comparisons", dependencies=[Depends(auth.require_csrf)])
def compare(body: CompareIn, user=Depends(auth.get_current_user)):
    if body.document_id_a == body.document_id_b:
        raise HTTPException(422, "Choose two different documents to compare.")
    a, b = get(body.document_id_a, user["id"]), get(body.document_id_b, user["id"])
    if a.get("doc_type") not in extract.LOAN_DOCUMENT_TYPES or b.get("doc_type") not in extract.LOAN_DOCUMENT_TYPES:
        raise HTTPException(409, "Loan offer comparison requires two supported loan documents. Use statement comparison for bank statements.")
    terms_a = comparison_terms(effective_terms(a["terms"]), body.overrides_a)
    terms_b = comparison_terms(effective_terms(b["terms"]), body.overrides_b)
    result = {"a": a["filename"], "b": b["filename"], "rows": cmp.compare(terms_a, terms_b)}
    saved = _persist_comparison(
        user["id"], "loan", [body.document_id_a, body.document_id_b],
        f"{a['filename']} vs {b['filename']}", body.model_dump(mode="json"), result,
    )
    return {**result, "comparison_id": saved["id"], "created_at": saved["created_at"]}


def _statement_comparison(body, owner_id):
    a, b = get(body.document_id_a, owner_id), get(body.document_id_b, owner_id)
    if not (str(a.get("doc_type", "")).startswith("bank_statement_")
            and str(b.get("doc_type", "")).startswith("bank_statement_")):
        raise HTTPException(409, "Choose two bank statements of supported types.")
    transactions_a = store.all_transactions(body.document_id_a, owner_id)
    transactions_b = store.all_transactions(body.document_id_b, owner_id)
    return statement_compare.compare_statements(a, transactions_a, b, transactions_b)


@app.post("/api/v1/comparisons/statements", dependencies=[Depends(auth.require_csrf)])
def compare_statements(body: CompareIn, user=Depends(auth.get_current_user)):
    if body.document_id_a == body.document_id_b:
        raise HTTPException(422, "Choose two different statements to compare.")
    result = _statement_comparison(body, user["id"])
    saved = _persist_comparison(
        user["id"], "statement", [body.document_id_a, body.document_id_b],
        f"{result['a']['filename']} vs {result['b']['filename']}", body.model_dump(mode="json"), result,
    )
    return {**result, "comparison_id": saved["id"], "created_at": saved["created_at"]}


@app.post("/api/v1/comparisons/financial-statements", dependencies=[Depends(auth.require_csrf)])
def compare_financial_statements(body: CompareIn, user=Depends(auth.get_current_user)):
    if body.document_id_a == body.document_id_b:
        raise HTTPException(422, "Choose two different financial statements to compare.")
    a, b = get(body.document_id_a, user["id"]), get(body.document_id_b, user["id"])
    if a.get("doc_type") != "financial_statement" or b.get("doc_type") != "financial_statement":
        raise HTTPException(409, "Choose two supported financial-statement documents.")
    result = financial_statements.compare_statements(
        a, effective_terms(a.get("terms", {})),
        b, effective_terms(b.get("terms", {})),
    )
    saved = _persist_comparison(
        user["id"], "financial_statement", [body.document_id_a, body.document_id_b],
        f"{a['filename']} vs {b['filename']}", body.model_dump(mode="json"), result,
    )
    return {**result, "comparison_id": saved["id"], "created_at": saved["created_at"]}

@app.get("/api/v1/comparisons")
def list_comparison_history(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                            kind: str | None = Query(None), user=Depends(auth.get_current_user)):
    if kind not in (None, "loan", "statement", "financial_statement"):
        raise HTTPException(422, "Comparison kind must be loan, statement, or financial_statement.")
    return store.list_comparisons(user["id"], offset, limit, kind)

@app.get("/api/v1/comparisons/{comparison_id}")
def get_comparison_history(comparison_id: str, user=Depends(auth.get_current_user)):
    try:
        return store.get_comparison(comparison_id, user["id"])
    except KeyError as exc:
        raise HTTPException(404, "Comparison not found") from exc

@app.get("/api/v1/comparisons/{comparison_id}/report")
def saved_comparison_report(comparison_id: str, user=Depends(auth.get_current_user)):
    try:
        record = store.get_comparison(comparison_id, user["id"])
    except KeyError as exc:
        raise HTTPException(404, "Comparison not found") from exc
    return _saved_comparison_pdf(record)

@app.delete("/api/v1/comparisons/{comparison_id}", status_code=204,
            dependencies=[Depends(auth.require_csrf)])
def remove_comparison_history(comparison_id: str, user=Depends(auth.get_current_user)):
    try:
        store.delete_comparison(comparison_id, user["id"])
    except KeyError as exc:
        raise HTTPException(404, "Comparison not found") from exc

def comparison_terms(original, overrides):
    """Apply comparison-only corrections while retaining the original evidence."""
    terms = {key: dict(value) for key, value in original.items()}
    if not overrides:
        return terms
    allowed = set(cmp.KEYS)
    unknown = set(overrides) - allowed
    if unknown:
        raise HTTPException(422, "Unsupported comparison field")
    for key, entered in overrides.items():
        raw = (entered or "").strip()
        if len(raw) > 80:
            raise HTTPException(422, "Comparison values must be 80 characters or fewer")
        term = dict(terms.get(key) or {"page": None, "evidence": None})
        if not raw:
            term.update(value=None, raw=None, status="manual", manual_source="comparison_override", note="No value entered for this comparison.")
            terms[key] = term
            continue
        match = re.search(r"-?\d[\d,]*(?:\.\d+)?", raw)
        if not match:
            raise HTTPException(422, f"Enter a numeric value for {key.replace('_', ' ')}")
        number = float(match.group().replace(",", ""))
        if not math.isfinite(number):
            raise HTTPException(422, "Enter a finite numeric value")
        if key == "tenure":
            if number <= 0 or not number.is_integer():
                raise HTTPException(422, "Tenure must be a positive whole number of months")
            value = int(number)
        elif key in ("loan_amount", "emi") and number <= 0:
            raise HTTPException(422, f"{key.replace('_', ' ').capitalize()} must be positive")
        elif key == "interest_rate" and number < 0:
            raise HTTPException(422, "Interest rate cannot be negative")
        elif key == "processing_fee" and number < 0:
            raise HTTPException(422, "Processing fee cannot be negative")
        else:
            value = raw if key == "processing_fee" else number
        term.update(value=value, raw=raw, status="manual", manual_source="comparison_override", note="User-entered comparison value; original source evidence is shown below.")
        terms[key] = term
    return terms

def _report_paragraph(text, style):
    value = str(text or "--").replace(chr(8377), "INR ").replace(chr(8212), "-").replace(chr(183), " - ")
    value = "".join(character if ord(character) < 256 else "?" for character in value)
    escaped = html.escape(value).replace(chr(10), "<br/> ")
    return Paragraph(escaped, style)


def _statement_comparison_source(source, label):
    if source is None:
        return f"{label}: transaction not present in this statement."
    location = (f"Page {source['page']}" if source.get("page") else
                f"{source['source_sheet']} row {source.get('row_number')}" if source.get("source_sheet") else
                f"CSV row {source.get('row_number')}")
    evidence = source.get("evidence") or "; ".join(
        f"{key}: {value}" for key, value in source.get("source_values", {}).items() if value
    )
    if len(evidence) > 700:
        evidence = evidence[:700] + " … [truncated]"
    return f"{label} · {location}: {evidence or 'No row passage available.'}"


def _statement_minor_text(value, currency):
    sign = "-" if value < 0 else ""
    return f"{sign}{abs(value) // 100:,}.{abs(value) % 100:02d} {currency}"


def _saved_comparison_pdf(record):
    result = record["result"]
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SavedComparisonBody", parent=styles["BodyText"], fontSize=8, leading=11))
    story = [
        _report_paragraph("BankEase AI - Saved comparison snapshot", styles["Title"]),
        _report_paragraph(f"Type: {record['kind']} · Saved: {record['created_at']}", styles["SavedComparisonBody"]),
        _report_paragraph(f"Document A: {result.get('a', record['title'].split(' vs ', 1)[0])}", styles["Heading2"]),
        _report_paragraph(f"Document B: {result.get('b', record['title'].split(' vs ', 1)[-1])}", styles["Heading2"]),
        _report_paragraph("This report reproduces the stored comparison result and evidence snapshot; it does not rerun extraction.", styles["SavedComparisonBody"]),
    ]
    if record["kind"] == "financial_statement":
        story.append(_report_paragraph(result.get("note"), styles["SavedComparisonBody"]))
        for side, label in (("a", "A"), ("b", "B")):
            period = result.get(side, {}).get("reporting_period", {})
            period_text = period.get("value") or ("Ambiguous; review the source." if period.get("status") == "ambiguous" else "Not found in extracted text.")
            page = f" · page {period['page']}" if period.get("page") else ""
            evidence = f" Evidence: {period['evidence']}" if period.get("evidence") else ""
            story.append(_report_paragraph(f"Reporting period {label}: {period_text}{page}.{evidence}", styles["SavedComparisonBody"]))
        summary = result.get("summary", {})
        story.append(_report_paragraph(
            f"Comparable fields: {summary.get('comparable_field_count', 0)}. "
            f"Potential differences: {summary.get('difference_count', 0)}. "
            f"Values needing review: {summary.get('needs_review_count', 0)}. "
            f"Values without a verified common unit: {summary.get('not_comparable_count', 0)}.",
            styles["SavedComparisonBody"],
        ))
        for row in result.get("rows", []):
            story.append(_report_paragraph(f"{row['label']}: {row['status'].replace('_', ' ')}", styles["Heading3"]))
            if row.get("difference_b_minus_a") is not None:
                story.append(_report_paragraph(
                    f"B minus A: {row['difference_b_minus_a']} {row['unit']['currency']} ({row['unit']['scale']}).",
                    styles["SavedComparisonBody"],
                ))
            for side, label in (("a", "A"), ("b", "B")):
                item = row[side]
                page = f" · page {item['page']}" if item.get("page") else ""
                story.append(_report_paragraph(
                    f"{label}: {item.get('value') or 'Not found'}{page}. "
                    f"Evidence: {item.get('evidence') or 'No source passage available.'}",
                    styles["SavedComparisonBody"],
                ))
    elif record["kind"] == "loan":
        story.append(Paragraph("Loan terms", styles["Heading2"]))
        for row in result.get("rows", []):
            story.append(_report_paragraph(
                f"{row['field'].replace('_', ' ').title()}: {row['status'].replace('_', ' ')}",
                styles["Heading3"],
            ))
            for side, label in (("a", "A"), ("b", "B")):
                item = row[side]
                value = item.get("value") or "Not found"
                page = f" · page {item['page']}" if item.get("page") else ""
                source_kind = _comparison_manual_label(item)
                story.append(_report_paragraph(
                    f"{label}: {value}{source_kind}{page}. Evidence: {item.get('evidence') or 'No source passage available.'}",
                    styles["SavedComparisonBody"],
                ))
    else:
        story.append(_report_paragraph(result.get("note"), styles["SavedComparisonBody"]))
        summary = result.get("summary", {})
        story.append(_report_paragraph(
            f"Matched rows: {summary.get('matched_transaction_count', 0)}. "
            f"Only in A: {summary.get('only_in_a_count', 0)}. "
            f"Only in B: {summary.get('only_in_b_count', 0)}. "
            f"Running-balance differences: {summary.get('running_balance_difference_count', 0)}.",
            styles["SavedComparisonBody"],
        ))
        for field, values in result.get("balance_comparison", {}).items():
            story.append(_report_paragraph(
                f"{field.replace('_minor', '').replace('_', ' ').title()}: "
                f"A {_statement_minor_text(values['a_minor'], result['a']['currency'])}; "
                f"B {_statement_minor_text(values['b_minor'], result['b']['currency'])}; "
                f"difference {_statement_minor_text(values['difference_minor'], result['a']['currency'])}.",
                styles["SavedComparisonBody"],
            ))
        for difference in result.get("differences", [])[:100]:
            story.append(_report_paragraph(
                f"{difference['kind'].replace('_', ' ').title()} · {difference['date']} · "
                f"{_statement_minor_text(difference['amount_minor'], result['a']['currency'])} · {difference['description']}",
                styles["Heading3"],
            ))
            story.append(_report_paragraph(_statement_comparison_source(difference.get("a"), "A"), styles["SavedComparisonBody"]))
            story.append(_report_paragraph(_statement_comparison_source(difference.get("b"), "B"), styles["SavedComparisonBody"]))
    if record.get("history_truncated"):
        story.append(_report_paragraph("This saved statement comparison retained a limited difference excerpt; summary counts reflect the full comparison.", styles["SavedComparisonBody"]))
    story.append(Spacer(1, 2 * mm))
    story.append(_report_paragraph("Differences are review signals, not proof of error or misconduct. Verify findings against the original documents.", styles["SavedComparisonBody"]))
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm,
                      topMargin=15 * mm, bottomMargin=15 * mm,
                      title="BankEase AI saved comparison snapshot").build(story)
    output.seek(0)
    return StreamingResponse(output, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="bankease-saved-comparison-{record["id"][:8]}.pdf"'
    })


@app.post("/api/v1/comparisons/statements/report", dependencies=[Depends(auth.require_csrf)])
def statement_comparison_report(body: CompareIn, user=Depends(auth.get_current_user)):
    if body.document_id_a == body.document_id_b:
        raise HTTPException(422, "Choose two different statements to compare.")
    result = _statement_comparison(body, user["id"])
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="StatementCompareBody", parent=styles["BodyText"], fontSize=8, leading=11))
    story = [
        _report_paragraph("BankEase AI - Statement comparison", styles["Title"]),
        _report_paragraph(f"Statement A: {result['a']['filename']}", styles["Heading2"]),
        _report_paragraph(f"Statement B: {result['b']['filename']}", styles["Heading2"]),
        _report_paragraph(result["note"], styles["StatementCompareBody"]),
    ]
    if result["comparable"]:
        summary = result["summary"]
        story.append(_report_paragraph(
            f"Currency: {result['a']['currency']}. Matched transaction rows: {summary['matched_transaction_count']}. "
            f"Only in A: {summary['only_in_a_count']}. Only in B: {summary['only_in_b_count']}. "
            f"Running-balance differences: {summary['running_balance_difference_count']}.",
            styles["StatementCompareBody"],
        ))
        if result["balance_comparison"]:
            story.append(Paragraph("Balance comparison", styles["Heading2"]))
            for field, values in result["balance_comparison"].items():
                label = "Implied opening balance" if values["a_is_implied"] else "Closing balance"
                story.append(_report_paragraph(
                    f"{label}: A {_statement_minor_text(values['a_minor'], result['a']['currency'])}; "
                    f"B {_statement_minor_text(values['b_minor'], result['b']['currency'])}; "
                    f"difference {_statement_minor_text(values['difference_minor'], result['a']['currency'])}.",
                    styles["StatementCompareBody"],
                ))
        story.append(Paragraph("Transaction differences for review", styles["Heading2"]))
        shown = result["differences"][:100]
        if not shown:
            story.append(_report_paragraph("No exact transaction or running-balance differences were detected.", styles["StatementCompareBody"]))
        for difference in shown:
            value = difference["amount_minor"]
            amount = _statement_minor_text(value, result["a"]["currency"])
            story.append(_report_paragraph(
                f"{difference['kind'].replace('_', ' ').title()} · {difference['date']} · {amount} · {difference['description']}",
                styles["Heading3"],
            ))
            story.append(_report_paragraph(_statement_comparison_source(difference["a"], "A"), styles["StatementCompareBody"]))
            story.append(_report_paragraph(_statement_comparison_source(difference["b"], "B"), styles["StatementCompareBody"]))
            story.append(Spacer(1, 2 * mm))
        if len(result["differences"]) > len(shown):
            story.append(_report_paragraph(f"The report lists the first {len(shown)} of {len(result['differences'])} review differences.", styles["StatementCompareBody"]))
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm,
                      topMargin=15 * mm, bottomMargin=15 * mm,
                      title="BankEase AI statement comparison").build(story)
    output.seek(0)
    return StreamingResponse(output, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="bankease-statement-comparison-{body.document_id_a[:8]}-{body.document_id_b[:8]}.pdf"'
    })

def _processing_fee_amount(term, principal):
    if not term or term.get("value") is None:
        return None
    raw = str(term.get("raw") or term.get("value"))
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return None
    amount = float(match.group().replace(",", ""))
    return principal * amount / 100 if "%" in raw else amount

def _comparison_finance(terms):
    principal = (terms.get("loan_amount") or {}).get("value")
    rate = (terms.get("interest_rate") or {}).get("value")
    months = (terms.get("tenure") or {}).get("value")
    stated = (terms.get("emi") or {}).get("value")
    summary = financial.summary(principal, rate, months, stated)
    fee = _processing_fee_amount(terms.get("processing_fee"), principal)
    summary["processing_fee_estimate"] = round(fee, 2) if fee is not None else None
    summary["repayment_plus_processing_fee"] = round(summary["total_repayment"] + fee, 2) if fee is not None else None
    return summary

def _comparison_manual_label(item):
    if item.get("manual_source") == "saved_correction":
        return " [saved correction]"
    return " [comparison override]" if item.get("manual") else ""

@app.post("/documents/compare/report", dependencies=[Depends(auth.require_csrf)])
@app.post("/api/v1/comparisons/report", dependencies=[Depends(auth.require_csrf)])
def compare_report(body: CompareIn, user=Depends(auth.get_current_user)):
    a, b = get(body.document_id_a, user["id"]), get(body.document_id_b, user["id"])
    if a.get("doc_type") not in extract.LOAN_DOCUMENT_TYPES or b.get("doc_type") not in extract.LOAN_DOCUMENT_TYPES:
        raise HTTPException(409, "Loan offer reports require two supported loan documents. Use statement comparison for bank statements.")
    terms_a = comparison_terms(effective_terms(a["terms"]), body.overrides_a)
    terms_b = comparison_terms(effective_terms(b["terms"]), body.overrides_b)
    rows = cmp.compare(terms_a, terms_b)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SmallCell", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9, alignment=TA_LEFT, wordWrap="CJK"))
    styles.add(ParagraphStyle(name="Evidence", parent=styles["BodyText"], fontSize=8, leading=11, textColor=colors.HexColor("#334155")))
    story = [_report_paragraph("BankEase AI - Offer comparison", styles["Title"]),
             Paragraph("Document-grounded comparison. Verify important values against the original agreements.", styles["BodyText"]),
             Spacer(1, 5 * mm), _report_paragraph(f"Offer A: {a['filename']}", styles["Heading2"]),
             _report_paragraph(f"Offer B: {b['filename']}", styles["Heading2"]), Spacer(1, 3 * mm)]
    header = ["Field", "Offer A - value / page", "Offer B - value / page", "Comparison"]
    data = [[_report_paragraph(item, styles["SmallCell"]) for item in header]]
    for row in rows:
        data.append([
            _report_paragraph(row["field"].replace("_", " ").title(), styles["SmallCell"]),
            _report_paragraph(f"{row['a']['value'] or 'Not found'}" + _comparison_manual_label(row["a"]) + (f" - p.{row['a']['page']}" if row["a"]["page"] else ""), styles["SmallCell"]),
            _report_paragraph(f"{row['b']['value'] or 'Not found'}" + _comparison_manual_label(row["b"]) + (f" - p.{row['b']['page']}" if row["b"]["page"] else ""), styles["SmallCell"]),
            _report_paragraph(row["status"].replace("_", " ").title(), styles["SmallCell"]),
        ])
    table = Table(data, colWidths=[31 * mm, 51 * mm, 51 * mm, 35 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eff6ff")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
        ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([table, Spacer(1, 6 * mm), Paragraph("Estimated repayment", styles["Heading2"])])
    for label, terms in (("Offer A", terms_a), ("Offer B", terms_b)):
        try:
            calc = _comparison_finance(terms)
            stated = "Not available" if calc["stated_emi"] is None else f"INR {calc['stated_emi']:,.2f}"
            difference = "Not available" if calc["difference"] is None else f"INR {calc['difference']:,.2f}"
            fee = "Not available" if calc["processing_fee_estimate"] is None else f"INR {calc['processing_fee_estimate']:,.2f}"
            total = "Not available" if calc["repayment_plus_processing_fee"] is None else f"INR {calc['repayment_plus_processing_fee']:,.2f}"
            story.append(_report_paragraph(f"{label}: calculated EMI INR {calc['calculated_emi']:,.2f}; stated EMI {stated}; stated minus calculated {difference}; total repayment INR {calc['total_repayment']:,.2f}; total interest INR {calc['total_interest']:,.2f}; processing fee estimate {fee}; repayment plus processing fee {total}.", styles["BodyText"]))
        except financial.FinancialError as exc:
            story.append(_report_paragraph(f"{label}: repayment estimate unavailable - {exc}.", styles["BodyText"]))
    story.extend([Spacer(1, 4 * mm), Paragraph("Source evidence", styles["Heading2"])])
    for row in rows:
        story.append(Paragraph(row["field"].replace("_", " ").title(), styles["Heading3"]))
        for label, item in (("Offer A", row["a"]), ("Offer B", row["b"])):
            page = f" (page {item['page']})" if item["page"] else ""
            evidence = item.get("evidence") or "No source text was extracted for this field."
            story.append(_report_paragraph(f"{label}{page}: {evidence}", styles["Evidence"]))
    manual_items = [f"{label} - {row['field'].replace('_', ' ')}: {row[side]['value'] or 'Not found'}{_comparison_manual_label(row[side])}"
                    for row in rows for side, label in (("a", "Offer A"), ("b", "Offer B")) if row[side].get("manual")]
    if manual_items:
        story.extend([Spacer(1, 4 * mm), Paragraph("Comparison-only edits", styles["Heading2"])])
        story.extend(_report_paragraph(item, styles["Evidence"]) for item in manual_items)
    story.extend([Spacer(1, 5 * mm), Paragraph("Calculation assumptions: reducing-balance method; monthly equal installments; nominal annual rate divided by 12. Fees other than the processing fee, insurance and rate changes are excluded. A percentage processing fee is estimated against the principal. Manually edited values are comparison inputs and are not verified against the source. This report is informational, not financial or legal advice.", styles["Evidence"])])
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm, topMargin=15 * mm, bottomMargin=15 * mm, title="BankEase AI offer comparison").build(story)
    output.seek(0)
    return StreamingResponse(output, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="bankease-comparison-{body.document_id_a[:8]}-{body.document_id_b[:8]}.pdf"'})

@app.get("/documents/{i}/report", response_class=HTMLResponse)
@app.get("/api/v1/documents/{i}/report", response_class=HTMLResponse)
def report(i: str, user=Depends(auth.get_current_user)):
    r, e = get(i, user["id"]), html.escape
    if str(r.get("doc_type", "")).startswith("bank_statement_"):
        analysis = r.get("statement_analysis", {})
        opening = analysis.get("opening_balance_minor")
        closing = analysis.get("closing_balance_minor")
        money = lambda value: "Not available" if value is None else f"{value / 100:,.2f} {e(analysis.get('currency', 'UNSPECIFIED'))}"
        extraction_status = e(analysis.get("extraction_status", "completed"))
        balance_note = e(analysis.get("balance_note", ""))
        issue_rows = "".join(
            f"<li>Source row {item['row_number']}: expected {money(item['expected_minor'])}; observed {money(item['observed_minor'])}.</li>"
            for item in analysis.get("balance_issues", [])
        ) or "<li>No running-balance differences detected, or balances were not supplied.</li>"
        duplicate_rows = "".join(
            f"<li>Rows {e(', '.join(str(row) for row in item['rows']))}: {e(item['date'])}, {money(item['amount_minor'])}, {e(item['description'])}.</li>"
            for item in analysis.get("duplicate_candidates", [])
        ) or "<li>No duplicate candidates detected.</li>"
        return ("<html><body style='font-family:Georgia;max-width:800px;margin:auto'>"
                f"<h1>BankEase AI statement summary</h1><p>{e(r['filename'])} - {r.get('transaction_count', 0)} transaction rows</p>"
                f"<p>Extraction status: {extraction_status}. Currency: {e(analysis.get('currency', 'UNSPECIFIED'))}. Balance check: {e(analysis.get('balance_check', 'unavailable'))}.</p>"
                f"<p>{balance_note}</p>"
                f"<p>Implied opening balance: {money(opening)} (derived from the first row); closing balance: {money(closing)}.</p>"
                f"<h2>Balance review</h2><ul>{issue_rows}</ul><h2>Potential duplicate candidates</h2><ul>{duplicate_rows}</ul>"
                "<p>These checks identify items for review; they do not establish error or misconduct.</p>"
                f"<p><small>{DISCLAIMER}</small></p></body></html>")
    term_rows = []
    for key, term in r["terms"].items():
        source_value = term.get("raw") or "Not found"
        correction = term.get("user_correction")
        displayed = f"{source_value} · corrected to {correction['raw']}" if correction else source_value
        status_label = "corrected" if correction else term.get("status", "unknown")
        term_rows.append(
            f"<tr><td>{e(key)}</td><td>{e(displayed)}</td><td>{term.get('page') or ''}</td><td>{e(status_label)}</td></tr>"
        )
    rows = "".join(term_rows)
    cl = "".join(f"<li><b>{e(c['category'])}</b> (p.{c['page']}): {e(c['text'])}<br><i>Attention point: {e(c['attention_point'])}</i></li>" for c in r["clauses"])
    financial_rows = ""
    if r.get("doc_type") == "financial_statement":
        checks = financial_statements.verify_totals(r.get("terms", {}))
        rows_html = "".join(
            f"<li>{e(check['label'])}: {e(check['status'])}. {e(check['summary'])} "
            + " ".join(f"(p.{e(str(source.get('page') or ''))}) {e(source.get('evidence') or '')}" for source in check.get("sources", []))
            + "</li>" for check in checks
        )
        financial_rows = f"<h2>Arithmetic checks</h2><ul>{rows_html}</ul>"
    return (f"<html><body style='font-family:Georgia;max-width:800px;margin:auto'><h1>BankEase AI report</h1><p>{e(r['filename'])} — {e(r['doc_type'])}, {r['page_count']} page(s)</p>"
            f"<h2>Extracted terms</h2><table border=1 cellpadding=4><tr><th>Field</th><th>Value</th><th>Page</th><th>Status</th></tr>{rows}</table>"
            f"{financial_rows}<h2>Clauses</h2><ul>{cl}</ul><h2>Limitations</h2><p>Rule-based extraction; verify against the original document.</p><p><small>{DISCLAIMER}</small></p></body></html>")

@app.delete("/documents/{i}", status_code=204, dependencies=[Depends(auth.require_csrf)])
@app.delete("/api/v1/documents/{i}", status_code=204, dependencies=[Depends(auth.require_csrf)])
def delete(i: str, request: Request, user=Depends(auth.get_current_user)):
    repo = auth._repo(request)
    get(i, user["id"])
    retention.delete_document_data(repo, user["id"], i)
