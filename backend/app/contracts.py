"""Owner-scoped contract analysis API built on saved document extraction."""
from __future__ import annotations

import copy
import html
import io
import logging
import threading
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import ai_engine, auth, contract_analysis_service as analysis_service, store

logger = logging.getLogger("bankease.contracts")
router = APIRouter(prefix="/api/v1/contracts", tags=["contract-analysis"])
_ANALYSIS_LOCK = threading.RLock()


class ContractCompareIn(BaseModel):
    document_ids: list[str] = Field(min_length=2, max_length=5)


class ContractReminderIn(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    due_date: date
    notes: str = Field(default="", max_length=500)


class ContractReminderPatch(BaseModel):
    status: str = Field(pattern="^(open|completed)$")


class ContractSettingsIn(BaseModel):
    severity_points: dict[str, int]
    category_multipliers: dict[str, float]
    frameworks: list[str] = Field(default_factory=list, max_length=8)


def _owner(user):
    return user["id"]


def _document(document_id, owner_id):
    try:
        return store.load(document_id, owner_id)
    except KeyError as exc:
        raise HTTPException(404, "Contract document not found") from exc


def _analysis(record):
    return record.get("contract_analysis") or {}


def _contract_records(owner_id):
    rows = store.list_owner(owner_id)
    return [row for row in rows if analysis_service.is_contract_candidate(row)
            or isinstance(row.get("contract_analysis"), dict)]


def _require_analysis(record):
    analysis = _analysis(record)
    if analysis.get("status") != "completed":
        raise HTTPException(409, "Run contract analysis and wait for it to complete before requesting this result.")
    return analysis


def _now():
    return datetime.now(timezone.utc).isoformat()


def _analysis_row(record):
    analysis = _analysis(record)
    risk = analysis.get("risk_assessment", {})
    return {
        "id": record["id"], "filename": record.get("filename", "Contract"),
        "doc_type": record.get("doc_type", "unknown"),
        "suggested_doc_type": record.get("suggested_doc_type"),
        "document_status": record.get("status", "unknown"),
        "analysis_status": analysis.get("status", "not_analyzed"),
        "analysis_progress": analysis.get("progress", 0),
        "contract_type": analysis.get("contract_type", "unclassified"),
        "risk_score": risk.get("score"), "risk_category": risk.get("category"),
        "risk_confidence": risk.get("confidence"),
        "expiration_date": next((fact.get("date") for fact in analysis.get("facts", [])
                                  if fact.get("field") == "expiration_date" and fact.get("date")), None),
        "risk_count": len(analysis.get("risks", [])),
        "updated_at": analysis.get("analyzed_at") or record.get("updated_at") or record.get("created_at"),
        "source_locator_type": record.get("source_locator_type") or analysis.get("source_locator_type", "page"),
        "page_count": record.get("page_count", analysis.get("page_count", 0)),
        "section_count": record.get("section_count", analysis.get("section_count", 0)),
    }


def _run_analysis(document_id, owner_id, risk_settings):
    try:
        record = _document(document_id, owner_id)
        previous = _analysis(record)
        reminders = previous.get("reminders", [])

        def save_progress(progress):
            latest = _document(document_id, owner_id)
            active = _analysis(latest)
            active.update(progress)
            active.setdefault("reminders", reminders)
            store.set_contract_analysis(document_id, owner_id, active)

        save_progress({"status": "running", "progress": 0, "started_at": _now(),
                       "document_id": document_id, "filename": record.get("filename", "Contract"),
                       "pages_total": len(record.get("pages") or []), "pages_processed": 0,
                       "source_locator_type": record.get("source_locator_type") or "page"})
        result = analysis_service.analyze_record(record, risk_settings, save_progress)
        result["reminders"] = reminders
        store.set_contract_analysis(document_id, owner_id, result)
        logger.info("contract_analysis_complete document_id=%s model=%s risk_score=%s findings=%s",
                    document_id, result.get("model"), result.get("risk_assessment", {}).get("score"),
                    len(result.get("risks", [])))
    except Exception as exc:
        logger.exception("contract_analysis_failed document_id=%s", document_id)
        try:
            current = _document(document_id, owner_id)
            failed = _analysis(current)
            failed.update({"status": "failed", "progress": failed.get("progress", 0),
                           "error": str(exc)[:500], "failed_at": _now()})
            store.set_contract_analysis(document_id, owner_id, failed)
        except Exception:
            logger.exception("contract_analysis_failure_state_save_failed document_id=%s", document_id)


@router.get("/overview")
def contract_overview(user=Depends(auth.get_current_user)):
    rows = _contract_records(_owner(user))
    now = date.today()
    expiring_soon = []
    statuses = {"not_analyzed": 0, "queued": 0, "running": 0, "completed": 0, "failed": 0, "needs_review": 0}
    risks = {"low": 0, "moderate": 0, "high": 0, "critical": 0}
    types = {}
    compliance = {}
    annualized = 0.0
    annualized_count = 0
    for record in rows:
        analysis = _analysis(record)
        status = analysis.get("status", "not_analyzed")
        if status not in statuses:
            status = "needs_review"
        statuses[status] += 1
        risk_category = analysis.get("risk_assessment", {}).get("category")
        if risk_category in risks:
            risks[risk_category] += 1
        contract_type = analysis.get("contract_type", record.get("suggested_doc_type") or "unclassified")
        types[contract_type] = types.get(contract_type, 0) + 1
        total = analysis.get("financial_analysis", {}).get("annualized_recurring_total")
        if total is not None:
            try:
                annualized += float(total)
                annualized_count += 1
            except (TypeError, ValueError):
                pass
        for check in analysis.get("compliance_checks", []):
            key = check.get("status", "requires manual review")
            compliance[key] = compliance.get(key, 0) + 1
        for fact in analysis.get("facts", []):
            if fact.get("field") != "expiration_date" or not fact.get("date"):
                continue
            try:
                expires = date.fromisoformat(fact["date"])
            except (ValueError, TypeError):
                continue
            days_left = (expires - now).days
            if 0 <= days_left <= 90:
                expiring_soon.append({"document_id": record["id"], "filename": record.get("filename"),
                                      "expiration_date": expires.isoformat(), "days_left": days_left,
                                      "evidence": fact.get("evidence")})
    expiring_soon.sort(key=lambda item: item["expiration_date"])
    return {
        "total_contracts": len(rows), "contracts_under_review": statuses["queued"] + statuses["running"] + statuses["needs_review"],
        "high_risk_contracts": risks["high"], "critical_risk_contracts": risks["critical"],
        "medium_risk_contracts": risks["moderate"], "low_risk_contracts": risks["low"],
        "expiring_soon": len(expiring_soon), "pending_approvals": statuses["needs_review"],
        "active_contracts": sum(1 for row in rows if _analysis(row).get("status") == "completed"),
        "status_distribution": statuses, "risk_distribution": risks,
        "contract_types": [{"type": key, "count": value} for key, value in sorted(types.items())],
        "expiring_timeline": expiring_soon,
        "annualized_recurring_exposure": round(annualized, 2) if annualized_count else None,
        "annualized_exposure_documents": annualized_count,
        "compliance_findings": compliance,
        "contracts": [_analysis_row(row) for row in sorted(rows, key=lambda item: item.get("filename", "").casefold())],
        "note": "Counts include only uploaded documents identified as contract candidates. Exposure totals include only explicitly cited monthly or annual amounts. Active here means analysis complete, not legal effectiveness.",
    }


@router.get("")
def list_contracts(user=Depends(auth.get_current_user)):
    rows = _contract_records(_owner(user))
    return {"contracts": [_analysis_row(row) for row in sorted(rows, key=lambda item: item.get("filename", "").casefold())]}


@router.post("/{document_id}/analysis", status_code=202, dependencies=[Depends(auth.require_csrf)])
def start_contract_analysis(document_id: str, background: BackgroundTasks,
                            force: bool = Query(False),
                            user=Depends(auth.get_current_user)):
    owner_id = _owner(user)
    record = _document(document_id, owner_id)
    if str(record.get("doc_type", "")).startswith("bank_statement_"):
        raise HTTPException(409, "Contract analysis is not available for bank statements.")
    if not record.get("pages"):
        raise HTTPException(409, "This document has no extracted text to analyze. Reprocess it or upload a readable contract file.")
    with _ANALYSIS_LOCK:
        existing = _analysis(record)
        if existing.get("status") in {"queued", "running"}:
            return {"status": existing["status"], "progress": existing.get("progress", 0), "document_id": document_id}
        if existing.get("status") == "completed" and not force:
            return {"status": "completed", "progress": 100, "document_id": document_id, "already_analyzed": True}
        local = ai_engine.status()["local"]
        if not local.get("available"):
            raise HTTPException(503, "Ollama is unavailable. Start the configured local Ollama service, then retry.")
        if not local.get("model_available"):
            raise HTTPException(503, f"Configured Ollama model '{local.get('configured_model') or 'unconfigured'}' is not installed.")
        prior = copy.deepcopy(existing)
        queued = {"status": "queued", "progress": 0, "document_id": document_id,
                  "filename": record.get("filename", "Contract"), "queued_at": _now(),
                  "pages_total": len(record.get("pages") or []), "pages_processed": 0,
                  "source_locator_type": record.get("source_locator_type") or "page",
                  "reminders": prior.get("reminders", [])}
        store.set_contract_analysis(document_id, owner_id, queued)
        background.add_task(_run_analysis, document_id, owner_id,
                            store.contract_analysis_settings(owner_id) or analysis_service.DEFAULT_RISK_SETTINGS)
    return {"status": "queued", "progress": 0, "document_id": document_id}


@router.get("/{document_id}/analysis")
def get_contract_analysis(document_id: str, user=Depends(auth.get_current_user)):
    record = _document(document_id, _owner(user))
    analysis = _analysis(record)
    return analysis or {"status": "not_analyzed", "document_id": document_id,
                        "filename": record.get("filename", "Contract")}


@router.get("/clauses")
def list_contract_clauses(document_id: str | None = None, user=Depends(auth.get_current_user)):
    rows = _contract_records(_owner(user))
    if document_id:
        rows = [row for row in rows if row["id"] == document_id]
    clauses = []
    for record in rows:
        analysis = _analysis(record)
        for item in analysis.get("clauses", []):
            clauses.append({**item, "document_id": record["id"], "filename": record.get("filename")})
    clauses.sort(key=lambda item: (item.get("category", ""), item.get("filename", ""), item.get("evidence", {}).get("page", 0)))
    return {"clauses": clauses, "contracts_analyzed": sum(_analysis(row).get("status") == "completed" for row in rows)}


@router.get("/risks")
def list_contract_risks(document_id: str | None = None, user=Depends(auth.get_current_user)):
    rows = _contract_records(_owner(user))
    if document_id:
        rows = [row for row in rows if row["id"] == document_id]
    risks = []
    for record in rows:
        for item in _analysis(record).get("risks", []):
            risks.append({**item, "document_id": record["id"], "filename": record.get("filename"),
                          "risk_score": _analysis(record).get("risk_assessment", {}).get("score")})
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    risks.sort(key=lambda item: (order.get(item.get("severity"), 4), item.get("filename", "")))
    return {"risks": risks}


@router.get("/compliance")
def list_contract_compliance(document_id: str | None = None, user=Depends(auth.get_current_user)):
    rows = _contract_records(_owner(user))
    if document_id:
        rows = [row for row in rows if row["id"] == document_id]
    checks = []
    for record in rows:
        for item in _analysis(record).get("compliance_checks", []):
            checks.append({**item, "document_id": record["id"], "filename": record.get("filename")})
    return {"checks": checks, "note": "These are evidence-linked review observations, not determinations of legal or regulatory compliance."}


@router.get("/obligations")
def list_contract_obligations(document_id: str | None = None, user=Depends(auth.get_current_user)):
    rows = _contract_records(_owner(user))
    if document_id:
        rows = [row for row in rows if row["id"] == document_id]
    obligations, reminders = [], []
    for record in rows:
        analysis = _analysis(record)
        for item in analysis.get("obligations", []):
            obligations.append({**item, "document_id": record["id"], "filename": record.get("filename"),
                                "kind": "contractual", "is_user_reminder": False})
        for item in analysis.get("reminders", []):
            reminders.append({**item, "document_id": record["id"], "filename": record.get("filename"),
                              "kind": "user_reminder", "is_user_reminder": True})
    obligations.sort(key=lambda item: (item.get("due_date") or "9999", item.get("filename", "")))
    reminders.sort(key=lambda item: (item.get("due_date") or "9999", item.get("filename", "")))
    return {"contractual_obligations": obligations, "user_reminders": reminders}


@router.get("/financial")
def list_contract_financial_analysis(document_id: str | None = None, user=Depends(auth.get_current_user)):
    rows = _contract_records(_owner(user))
    if document_id:
        rows = [row for row in rows if row["id"] == document_id]
    results = []
    for record in rows:
        data = _analysis(record).get("financial_analysis")
        if data:
            results.append({"document_id": record["id"], "filename": record.get("filename"), **data})
    return {"financial_analysis": results}


@router.get("/reports")
def list_contract_reports(user=Depends(auth.get_current_user)):
    rows = _contract_records(_owner(user))
    return {"reports": [{"document_id": record["id"], "filename": record.get("filename"),
                          "status": _analysis(record).get("status", "not_analyzed"),
                          "analyzed_at": _analysis(record).get("analyzed_at"),
                          "available_reports": ["executive", "detailed", "risks", "clauses", "compliance"]
                          if _analysis(record).get("status") == "completed" else []} for record in rows]}


@router.post("/compare", dependencies=[Depends(auth.require_csrf)])
def compare_contracts(body: ContractCompareIn, user=Depends(auth.get_current_user)):
    if len(set(body.document_ids)) != len(body.document_ids):
        raise HTTPException(422, "Select each contract only once.")
    records = []
    for document_id in body.document_ids:
        record = _document(document_id, _owner(user))
        records.append((record, _require_analysis(record)))
    return analysis_service.compare_analyses(records)


@router.get("/settings")
def get_contract_settings(user=Depends(auth.get_current_user)):
    saved = store.contract_analysis_settings(_owner(user))
    current = saved or copy.deepcopy(analysis_service.DEFAULT_RISK_SETTINGS)
    current.setdefault("frameworks", ["Internal review"])
    current["scoring_method"] = "severity points × category multiplier, summed and capped at 100; missing information does not add risk points"
    return current


@router.put("/settings", dependencies=[Depends(auth.require_csrf)])
def update_contract_settings(body: ContractSettingsIn, user=Depends(auth.get_current_user)):
    severity_keys = set(analysis_service.SEVERITIES)
    category_keys = set(analysis_service.RISK_CATEGORIES)
    if set(body.severity_points) != severity_keys or set(body.category_multipliers) != category_keys:
        raise HTTPException(422, "Provide one severity-point value and category multiplier for every supported risk level and category.")
    if any(value < 0 or value > 50 for value in body.severity_points.values()):
        raise HTTPException(422, "Severity points must be from 0 to 50.")
    if any(value < 0 or value > 3 for value in body.category_multipliers.values()):
        raise HTTPException(422, "Category multipliers must be from 0 to 3.")
    frameworks = list(dict.fromkeys(value.strip() for value in body.frameworks if value.strip()))
    if any(len(value) > 100 for value in frameworks):
        raise HTTPException(422, "Framework labels must be 100 characters or fewer.")
    saved = {"severity_points": body.severity_points,
             "category_multipliers": body.category_multipliers,
             "frameworks": frameworks}
    store.save_contract_analysis_settings(_owner(user), saved)
    updated = 0
    for record in _contract_records(_owner(user)):
        analysis = _analysis(record)
        if analysis.get("status") != "completed":
            continue
        analysis = analysis_service.refresh_risk_score(analysis, saved)
        store.set_contract_analysis(record["id"], _owner(user), analysis)
        updated += 1
    return {**saved, "updated_contracts": updated}


@router.post("/{document_id}/reminders", status_code=201,
             dependencies=[Depends(auth.require_csrf)])
def create_contract_reminder(document_id: str, body: ContractReminderIn,
                             user=Depends(auth.get_current_user)):
    owner_id = _owner(user)
    record = _document(document_id, owner_id)
    analysis = _require_analysis(record)
    reminders = analysis.setdefault("reminders", [])
    reminder = {"id": uuid.uuid4().hex, "title": body.title.strip(),
                "due_date": body.due_date.isoformat(), "notes": body.notes.strip(),
                "status": "open", "created_at": _now(), "created_by": owner_id,
                "source": "user_created"}
    reminders.append(reminder)
    store.set_contract_analysis(document_id, owner_id, analysis)
    return reminder


@router.patch("/{document_id}/reminders/{reminder_id}", dependencies=[Depends(auth.require_csrf)])
def update_contract_reminder(document_id: str, reminder_id: str,
                             body: ContractReminderPatch,
                             user=Depends(auth.get_current_user)):
    owner_id = _owner(user)
    record = _document(document_id, owner_id)
    analysis = _require_analysis(record)
    reminder = next((item for item in analysis.get("reminders", []) if item.get("id") == reminder_id), None)
    if not reminder:
        raise HTTPException(404, "Reminder not found")
    reminder["status"] = body.status
    reminder["updated_at"] = _now()
    store.set_contract_analysis(document_id, owner_id, analysis)
    return reminder


def _build_pdf(title: str, record: dict, analysis: dict, report_kind: str):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=17 * mm, bottomMargin=17 * mm, title=title)
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("ContractReportTitle", parent=base["Title"], textColor=colors.HexColor("#14213d"), alignment=TA_LEFT, spaceAfter=5 * mm),
        "heading": ParagraphStyle("ContractReportHeading", parent=base["Heading2"], textColor=colors.HexColor("#24447a"), spaceBefore=4 * mm, spaceAfter=2 * mm),
        "body": ParagraphStyle("ContractReportBody", parent=base["BodyText"], fontSize=8.5, leading=12, spaceAfter=2 * mm),
        "small": ParagraphStyle("ContractReportSmall", parent=base["BodyText"], fontSize=7.2, leading=10, textColor=colors.HexColor("#526176"), spaceAfter=1.5 * mm),
    }

    def para(value, style="body"):
        safe = html.escape(str(value or "—")).replace("\n", "<br/>")
        return Paragraph(safe, styles[style])

    story = [para(title, "title"), para(record.get("filename", "Contract")),
             para(f"Analysis date: {analysis.get('analyzed_at', 'Unavailable')} · Model: {analysis.get('model', 'Unavailable')}", "small")]
    if report_kind in {"executive", "detailed"}:
        story += [para("Executive Summary", "heading"), para(analysis.get("executive_summary"))]
        facts = analysis.get("facts", [])
        if facts:
            story.append(para("Key Contract Details", "heading"))
            data = [[para("Field", "small"), para("Extracted value", "small"), para("Source", "small")]]
            data.extend([[para(item.get("field"), "small"), para(item.get("value"), "small"),
                          para(f"{analysis.get('source_locator_type', 'page').title()} {item.get('evidence', {}).get('page')}: {item.get('evidence', {}).get('quote')}", "small")]
                         for item in facts])
            table = Table(data, colWidths=[35 * mm, 48 * mm, 91 * mm], repeatRows=1)
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9eff8")),
                                       ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#d2dbe8")),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
            story.append(table)
    if report_kind in {"executive", "detailed", "risks"}:
        story.append(para("Risk Assessment", "heading"))
        risk = analysis.get("risk_assessment", {})
        story.append(para(f"AI-assisted score: {risk.get('score', '—')}/100 ({risk.get('category', 'unavailable')}); evidence confidence {risk.get('confidence', '—')}%."))
        story.append(para(risk.get("method", ""), "small"))
        for item in analysis.get("risks", []):
            story.extend([para(f"{item.get('severity', '').title()} · {item.get('category', '').title()} · {item.get('title', '')}", "heading"),
                          para(item.get("explanation")), para(f"Potential impact: {item.get('potential_impact', 'Not stated')}", "small"),
                          para(f"Recommended action: {item.get('recommended_action', 'Review manually')}", "small"),
                          para(f"Evidence — {analysis.get('source_locator_type', 'page').title()} {item.get('evidence', {}).get('page')}: {item.get('evidence', {}).get('quote')}", "small")])
    if report_kind in {"executive", "detailed", "clauses"}:
        story.append(para("Extracted Clauses", "heading"))
        for item in analysis.get("clauses", []):
            story.extend([para(f"{item.get('category')}: {item.get('name')} {item.get('clause_number', '')}", "heading"),
                          para(item.get("plain_english")), para(f"Original passage — {analysis.get('source_locator_type', 'page').title()} {item.get('evidence', {}).get('page')}: {item.get('original_text')}", "small")])
    if report_kind in {"executive", "detailed"}:
        story.append(para("Obligations and Deadlines", "heading"))
        for item in analysis.get("obligations", []):
            deadline = item.get("due_date") or item.get("raw_deadline") or "No clear date extracted"
            story.append(para(f"{item.get('party') or 'Party not specified'} — {item.get('obligation')} · {deadline} · {analysis.get('source_locator_type', 'page').title()} {item.get('evidence', {}).get('page')}", "small"))
        story.append(para("Financial Analysis", "heading"))
        financial = analysis.get("financial_analysis", {})
        story.append(para(f"Annualized recurring amount (where explicit): {financial.get('annualized_recurring_total', 'Not calculated')}. {financial.get('note', '')}"))
        for item in financial.get("terms", []):
            story.append(para(f"{item.get('label')}: {item.get('amount_text')} · source {item.get('evidence', {}).get('page')}", "small"))
    if report_kind in {"executive", "detailed", "compliance"}:
        story.append(para("Compliance Review Observations", "heading"))
        for item in analysis.get("compliance_checks", []):
            source = item.get("evidence")
            citation = f" · source {source.get('page')}: {source.get('quote')}" if source else " · no supporting provision identified"
            story.append(para(f"{item.get('framework')} — {item.get('requirement')} · {item.get('status')}. {item.get('explanation')}{citation}", "small"))
    if analysis.get("missing_information"):
        story.append(para("Missing or Ambiguous Information", "heading"))
        for item in analysis["missing_information"]:
            story.append(para(f"• {item}", "small"))
    story += [Spacer(1, 5 * mm), para(analysis.get("analysis_disclaimer", analysis_service.ANALYSIS_DISCLAIMER), "small")]
    doc.build(story)
    buffer.seek(0)
    return buffer


@router.get("/{document_id}/report")
def download_contract_report(document_id: str, kind: str = Query("executive", pattern="^(executive|detailed|risks|clauses|compliance)$"),
                             user=Depends(auth.get_current_user)):
    record = _document(document_id, _owner(user))
    analysis = _require_analysis(record)
    title = {"executive": "Executive Contract Summary", "detailed": "Detailed Contract Analysis",
             "risks": "Contract Risk Assessment", "clauses": "Contract Clause Report",
             "compliance": "Contract Compliance Review"}[kind]
    pdf = _build_pdf(title, record, analysis, kind)
    name = str(record.get("filename", "contract")).rsplit(".", 1)[0][:80]
    return StreamingResponse(pdf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{name}-{kind}.pdf"'})


@router.post("/compare/report", dependencies=[Depends(auth.require_csrf)])
def download_contract_comparison_report(body: ContractCompareIn, user=Depends(auth.get_current_user)):
    result = compare_contracts(body, user)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=17 * mm, bottomMargin=17 * mm, title="Contract Comparison")
    styles = getSampleStyleSheet()
    story = [Paragraph("Contract Comparison Report", styles["Title"]),
             Paragraph(html.escape(", ".join(item["filename"] for item in result["contracts"])), styles["BodyText"]),
             Spacer(1, 5 * mm), Paragraph(html.escape(result["note"]), styles["BodyText"]), Spacer(1, 4 * mm)]
    for row in result["rows"]:
        story.append(Paragraph(html.escape(f"{row['label']} · {row['status']}"), styles["Heading3"]))
        for contract, value in zip(result["contracts"], row["values"]):
            story.append(Paragraph(html.escape(f"{contract['filename']}: {value.get('value') or 'Not extracted'}"), styles["BodyText"]))
            evidence = value.get("evidence")
            evidences = evidence if isinstance(evidence, list) else ([evidence] if evidence else [])
            for source in evidences:
                if source:
                    story.append(Paragraph(html.escape(f"Source {source.get('page')}: {source.get('quote')}"), styles["Italic"]))
        story.append(Spacer(1, 2 * mm))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(html.escape(analysis_service.ANALYSIS_DISCLAIMER), styles["BodyText"]))
    doc.build(story)
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf",
                             headers={"Content-Disposition": 'attachment; filename="contract-comparison.pdf"'})

