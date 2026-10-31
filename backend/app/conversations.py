"""Owner-scoped, evidence-backed conversations about saved documents."""
from datetime import datetime, timezone
import json
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from fastapi.responses import StreamingResponse

from . import ai_engine, auth, qa, store


router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])
ai_router = APIRouter(prefix="/api/v1/ai", tags=["ai"])
SUPPORTED_DOCUMENT_TYPES = {"loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure", "insurance_policy", "financial_statement", "audit_report"}
logger = logging.getLogger("bankease.ai")


def _repo(request):
    from .auth_store import AuthRepository

    return AuthRepository(db=getattr(request.app.state, "mongo_database", None), root=store.ROOT)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _conversation_out(row):
    document_ids = row.get("document_ids") or ([row["document_id"]] if row.get("document_id") else [])
    filenames = row.get("document_filenames") or ([row.get("document_filename", "Document")] if document_ids else [])
    return {
        "id": row.get("_id", row.get("id")),
        "document_id": document_ids[0] if document_ids else None,
        "document_ids": document_ids,
        "document_filename": " + ".join(filenames)[:500] if filenames else "General conversation",
        "document_filenames": filenames,
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "message_count": row.get("message_count", 0),
        "mode": row.get("mode", "local"),
    }


def _message_out(row):
    result = {key: row[key] for key in ("role", "text", "sources", "created_at", "provider", "model", "mode", "web_research_used") if key in row}
    result["id"] = row.get("id", row.get("_id"))
    return result


class ConversationCreate(BaseModel):
    document_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    document_ids: list[str] = Field(default_factory=list, max_length=5)
    title: str = Field(default="New conversation", min_length=1, max_length=120)
    mode: str = Field(default="local", pattern=r"^(local|hosted|auto|hybrid)$")

    @model_validator(mode="after")
    def validate_documents(self):
        ids = list(self.document_ids)
        if self.document_id and self.document_id not in ids:
            ids.insert(0, self.document_id)
        if len(ids) > 5:
            raise ValueError("Select no more than five documents.")
        if len(ids) != len(set(ids)) or any(not re.fullmatch(r"[0-9a-f]{32}", item) for item in ids):
            raise ValueError("Document IDs must be unique, valid document IDs.")
        self.document_ids = ids
        self.document_id = ids[0] if ids else None
        return self


class MessageCreate(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    mode: str = Field(default="local", pattern=r"^(local|hosted|auto|hybrid)$")
    allow_hosted: bool = False
    allow_hosted_documents: bool = False
    web_research: bool = False
    model: str | None = Field(default=None, max_length=200)
    request_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


def _load_documents(document_ids: list[str], owner_id: str, require_ready: bool = True):
    documents = []
    for document_id in document_ids:
        try:
            document = store.load(document_id, owner_id)
        except KeyError as exc:
            raise HTTPException(404, "A source document for this conversation is unavailable") from exc
        if require_ready and document.get("status") not in {"completed", "needs_review"}:
            raise HTTPException(409, "Document analysis is not available for questions yet.")
        is_statement = str(document.get("doc_type", "")).startswith("bank_statement_")
        is_section_text = document.get("source_locator_type") == "section" and document.get("source_format") == "docx"
        if require_ready and not is_statement and not is_section_text and (document.get("doc_type") not in SUPPORTED_DOCUMENT_TYPES or not document.get("pages")):
            raise HTTPException(409, "A selected source document no longer supports text questions.")
        if require_ready and is_section_text and not document.get("pages"):
            raise HTTPException(409, "This DOCX has no extracted text to ask about.")
        if require_ready and is_statement and not document.get("transaction_count"):
            raise HTTPException(409, "This statement has no extracted transaction rows to ask about.")
        documents.append(document)
    return documents


def _statement_source(document: dict, row: dict) -> dict:
    effective = row.get("effective") or row
    details = [
        f"Date: {effective.get('date') or row.get('date') or 'unavailable'}",
        f"Description: {effective.get('description') or row.get('description') or 'unavailable'}",
        f"Amount minor units: {effective.get('amount_minor', row.get('amount_minor'))}",
        f"Debit minor units: {effective.get('debit_minor', row.get('debit_minor'))}",
        f"Credit minor units: {effective.get('credit_minor', row.get('credit_minor'))}",
        f"Balance minor units: {effective.get('balance_minor', row.get('balance_minor'))}",
    ]
    if row.get("evidence"):
        details.append(f"Recognized source passage: {row['evidence']}")
    source_values = row.get("source_values") or {}
    if source_values:
        details.append("Original cell values: " + "; ".join(f"{key}={value}" for key, value in source_values.items()))
    if row.get("source_sheet"):
        location = f"worksheet {row['source_sheet']}, row {row.get('row_number')}"
    elif row.get("page"):
        location = f"page {row['page']}"
    else:
        location = f"source row {row.get('row_number')}"
    return {
        "document_id": document["id"], "filename": document.get("filename", "Statement"),
        "page": row.get("page"), "row_number": row.get("row_number"),
        "source_sheet": row.get("source_sheet"), "kind": "document", "location": location,
        "text": "\n".join(details)[:1800],
    }


def _retrieve_statement_rows(question: str, documents: list[dict], owner_id: str, limit: int = 4) -> dict:
    terms = set(qa.toks(question))
    terms.difference_update({"statement", "transaction", "show", "find", "list", "tell", "please", "row"})
    scored = []
    summary_sources = []
    summary_answers = []
    for document in documents:
        rows = store.all_transactions(document["id"], owner_id)
        analysis = document.get("statement_analysis") or {}
        currency = document.get("currency") or analysis.get("currency") or "UNSPECIFIED"
        question_lower = question.casefold()
        if re.search(r"\b(balance|reconcil|opening|closing)\b", question_lower):
            check = analysis.get("balance_check") or document.get("reconciliation_status", "unavailable")
            balance_issues = analysis.get("balance_issues") or []
            opening = analysis.get("opening_balance_minor")
            closing = analysis.get("closing_balance_minor")
            summary = (f"Deterministic statement reconciliation status: {check}. Imported rows: {len(rows)}. "
                       f"Opening balance in minor units: {opening if opening is not None else 'unavailable'}. "
                       f"Closing balance in minor units: {closing if closing is not None else 'unavailable'}. "
                       f"Balance differences flagged: {len(balance_issues)}. This checks arithmetic within the imported statement; it does not verify records with the bank.")
            summary_sources.append({
                "document_id": document["id"], "filename": document.get("filename", "Statement"),
                "page": None, "row_number": None, "kind": "calculation", "location": "statement reconciliation",
                "text": summary,
            })
            if balance_issues:
                rows_text = ", ".join(f"row {issue.get('row_number')}" for issue in balance_issues[:10])
                result = f"The imported statement has arithmetic balance differences at {rows_text}. This is a review signal, not proof of an error or wrongdoing."
            elif check == "consistent":
                result = "The imported transaction rows reconcile with their listed running balances. This is an arithmetic check, not bank verification."
            else:
                result = "The imported statement does not contain enough consistent balance data to complete reconciliation."
            summary_answers.append(result)
        direction = None
        if re.search(r"\b(total|sum|how much|overall)\b", question_lower) and re.search(
                r"\b(debit|spend|spent|withdraw|withdrawal|expense|credit|deposit|income)\w*\b", question_lower):
            direction = "debit" if re.search(r"\b(debit|spend|spent|withdraw|withdrawal|expense)\w*\b", question_lower) else "credit"
        if direction:
            total = sum(max(0, int((row.get("effective") or row).get(f"{direction}_minor") or 0)) for row in rows)
            amount = abs(total)
            major, minor = divmod(amount, 100)
            major_text = str(major)
            if len(major_text) > 3:
                prefix, last = major_text[:-3], major_text[-3:]
                grouped = []
                while prefix:
                    grouped.append(prefix[-2:])
                    prefix = prefix[:-2]
                major_text = ",".join(reversed(grouped)) + "," + last
            formatted = f"{major_text}.{minor:02d} {currency}" if currency != "UNSPECIFIED" else f"{major_text}.{minor:02d} (currency not specified)"
            calc_text = (f"BankEase AI calculated total {direction} amount {formatted} across {len(rows)} imported transaction rows "
                         f"in {document.get('filename', 'the statement')}, using the current effective values. It is a calculation from the imported rows, not a bank-confirmed total.")
            summary_sources.append({
                "document_id": document["id"], "filename": document.get("filename", "Statement"),
                "page": None, "row_number": None, "kind": "calculation", "location": "calculation across imported rows",
                "text": calc_text,
            })
            summary_answers.append(f"Calculated total {direction}: {formatted} across {len(rows)} imported transaction rows. This is not a bank-confirmed total.")
        for row in rows:
            effective = row.get("effective") or row
            searchable = " ".join([
                str(effective.get("date", row.get("date", ""))),
                str(effective.get("description", row.get("description", ""))),
                str(row.get("row_number", "")),
                str(effective.get("amount_minor", row.get("amount_minor", ""))),
                str(effective.get("debit_minor", row.get("debit_minor", ""))),
                str(effective.get("credit_minor", row.get("credit_minor", ""))),
                " ".join(str(value) for value in (row.get("source_values") or {}).values()),
            ])
            matched = terms.intersection(qa.toks(searchable))
            coverage = len(matched) / len(terms) if terms else 0
            if matched and coverage >= 0.25:
                scored.append((len(matched), coverage, _statement_source(document, row)))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]["document_id"], item[2].get("row_number") or 0))
    row_sources = [item[2] for item in scored[:limit]]
    sources = summary_sources + row_sources
    if not sources:
        return {"found": False, "answer": "Not found in the selected statement rows.", "sources": [],
                "mode": "statement-row-search"}
    answer_parts = [f"{answer} [D{index}]" for index, answer in enumerate(summary_answers, 1)]
    answer_parts.extend(
        f'- {source["filename"]}, {source["location"]}: {source["text"].replace(chr(10), "; ")} [D{index}]'
        for index, source in enumerate(row_sources, len(summary_sources) + 1)
    )
    answer_text = "\n".join(answer_parts)
    return {"found": True, "answer": answer_text, "sources": sources, "mode": "statement-row-search"}


def _retrieved(question: str, documents: list[dict], owner_id: str):
    if not documents:
        return {"found": False, "answer": "", "sources": [], "mode": "extractive-fallback"}
    statement_documents = [document for document in documents
                           if str(document.get("doc_type", "")).startswith("bank_statement_")]
    text_documents = [document for document in documents if document not in statement_documents]
    if len(text_documents) == 1:
        text_result = (qa.answer_sections(question, text_documents[0].get("pages", []))
                       if text_documents[0].get("source_locator_type") == "section"
                       else qa.answer(question, text_documents[0].get("pages", [])))
        for source in text_result["sources"]:
            source.update({"document_id": text_documents[0]["id"],
                           "filename": text_documents[0].get("filename", "Document"), "kind": "document"})
    elif text_documents:
        text_result = qa.answer_documents(question, text_documents)
        for source in text_result["sources"]:
            source["kind"] = "document"
    else:
        text_result = {"found": False, "answer": "Not found in the selected documents.",
                       "sources": [], "mode": "extractive-fallback"}
    if not statement_documents:
        return text_result
    statement_result = _retrieve_statement_rows(question, statement_documents, owner_id)
    if not statement_result["sources"]:
        return text_result if text_documents else statement_result
    if not text_documents:
        return statement_result
    statement_answer = re.sub(
        r"\[D(\d+)\]",
        lambda match: f"[D{int(match.group(1)) + len(text_result['sources'])}]",
        statement_result["answer"],
    )
    answers = ([text_result["answer"]] if text_result.get("found") else [])
    if statement_result.get("found"):
        answers.append(statement_answer)
    return {"found": bool(answers), "answer": "\n\n".join(answers),
            "sources": text_result["sources"] + statement_result["sources"], "mode": "extractive-fallback"}


def _history(repository, owner_id: str, conversation_id: str):
    rows = repository.find_many("messages", {"owner_id": owner_id, "conversation_id": conversation_id}, "created_at")
    return [{"role": row.get("role"), "text": row.get("text", "")} for row in rows[-8:]]


def _ai_result(question: str, conversation: dict, documents: list[dict], history: list[dict],
               body: MessageCreate, owner_id: str):
    retrieved = _retrieved(question, documents, owner_id)
    try:
        generated = ai_engine.answer(
            question, history, retrieved["sources"], body.mode, body.allow_hosted,
            body.allow_hosted_documents, body.web_research,
            document_context_selected=bool(documents),
            requested_model=body.model,
            document_filenames=[document.get("filename", "Document") for document in documents],
            document_types=[document.get("doc_type", "unknown") for document in documents],
        )
    except ai_engine.EngineError as exc:
        if not documents or not retrieved["sources"]:
            raise HTTPException(exc.status_code, str(exc)) from exc
        # Existing deterministic retrieval remains useful when a local model is unavailable.
        retrieved["provider"] = "extractive"
        retrieved["model"] = None
        retrieved["notice"] = str(exc)
        return retrieved
    return {
        "answer": generated["answer"],
        "found": bool(generated["sources"] or not documents),
        "mode": generated["mode"],
        "provider": generated["provider"],
        "model": generated["model"],
        "web_research_requested": generated["web_research_requested"],
        "web_research_used": generated["web_research_used"],
        "web_research_note": generated["web_research_note"],
        "sources": generated["sources"],
    }


@ai_router.get("/status")
def ai_status(user=Depends(auth.get_current_user)):
    del user
    return ai_engine.status()


@ai_router.get("/health")
def ai_health(user=Depends(auth.get_current_user)):
    del user
    current = ai_engine.status()
    local = current["local"]
    ready = bool(local["available"] and local["model_available"])
    return {"status": "ok" if ready else "unavailable", "provider": "ollama",
            "model": local["configured_model"] or None, "model_available": local["model_available"],
            "ollama_available": local["available"]}


@router.get("")
def list_conversations(request: Request, limit: int = Query(30, ge=1, le=100), user=Depends(auth.get_current_user)):
    rows = _repo(request).find_many("conversations", {"owner_id": user["id"]}, "updated_at", True)[:limit]
    return {"conversations": [_conversation_out(row) for row in rows]}


@router.post("", status_code=201, dependencies=[Depends(auth.require_csrf)])
def create_conversation(body: ConversationCreate, request: Request, user=Depends(auth.get_current_user)):
    documents = []
    for document_id in body.document_ids:
        try:
            document = store.load(document_id, user["id"])
        except KeyError as exc:
            raise HTTPException(404, "Document not found") from exc
        is_statement = str(document.get("doc_type", "")).startswith("bank_statement_")
        is_section_text = document.get("source_locator_type") == "section" and document.get("source_format") == "docx"
        if not is_statement and not is_section_text and (document.get("doc_type") not in SUPPORTED_DOCUMENT_TYPES or not document.get("pages")):
            raise HTTPException(409, "Choose supported loan or insurance documents with extracted page text for this assistant.")
        if is_section_text and not document.get("pages"):
            raise HTTPException(409, "This DOCX has no extracted text to ask about.")
        if document.get("status") not in {"completed", "needs_review"}:
            raise HTTPException(409, "Wait until every selected document has finished analysis before starting a conversation.")
        if is_statement and not document.get("transaction_count"):
            raise HTTPException(409, "This statement has no extracted transaction rows to ask about.")
        documents.append(document)
    filenames = [document.get("filename", "Document") for document in documents]
    now, conversation_id = _now(), uuid.uuid4().hex
    row = {
        "_id": conversation_id,
        "owner_id": user["id"],
        "document_id": body.document_ids[0] if body.document_ids else None,
        "document_ids": body.document_ids,
        "document_filename": filenames[0] if filenames else "General conversation",
        "document_filenames": filenames,
        "title": body.title.strip() or "New conversation",
        "mode": body.mode,
        "message_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    _repo(request).insert_one("conversations", row)
    return _conversation_out(row)


@router.get("/{conversation_id}/messages")
def list_messages(conversation_id: str, request: Request, offset: int = Query(0, ge=0),
                  limit: int = Query(100, ge=1, le=200), user=Depends(auth.get_current_user)):
    repository = _repo(request)
    conversation = repository.find_one("conversations", {"_id": conversation_id, "owner_id": user["id"]})
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    document_ids = conversation.get("document_ids") or ([conversation["document_id"]] if conversation.get("document_id") else [])
    _load_documents(document_ids, user["id"], require_ready=False)
    rows = repository.find_many("messages", {"owner_id": user["id"], "conversation_id": conversation_id},
                                "created_at")
    return {"conversation": _conversation_out(conversation), "messages": [_message_out(row) for row in rows[offset:offset + limit]],
            "offset": offset, "limit": limit, "total": len(rows)}


@router.post("/{conversation_id}/messages", dependencies=[Depends(auth.require_csrf)])
def create_message(conversation_id: str, body: MessageCreate, request: Request,
                   user=Depends(auth.get_current_user)):
    repository = _repo(request)
    query = {"_id": conversation_id, "owner_id": user["id"]}
    conversation = repository.find_one("conversations", query)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    question = body.question.strip()
    document_ids = conversation.get("document_ids") or ([conversation["document_id"]] if conversation.get("document_id") else [])
    documents = _load_documents(document_ids, user["id"])
    result = _ai_result(question, conversation, documents,
                         _history(repository, user["id"], conversation_id), body, user["id"])
    now = _now()
    user_message = {
        "_id": uuid.uuid4().hex, "owner_id": user["id"], "conversation_id": conversation_id,
        "role": "user", "text": question, "sources": [], "created_at": now,
    }
    assistant_message = {
        "_id": uuid.uuid4().hex, "owner_id": user["id"], "conversation_id": conversation_id,
        "role": "assistant", "text": result["answer"], "sources": result["sources"], "created_at": _now(),
        "provider": result.get("provider", "extractive"), "model": result.get("model"),
        "mode": result.get("mode", "extractive-fallback"),
        "web_research_used": result.get("web_research_used", False),
    }
    repository.insert_one("messages", user_message)
    repository.insert_one("messages", assistant_message)
    repository.update_one("conversations", query, {
        "updated_at": assistant_message["created_at"],
        "message_count": int(conversation.get("message_count", 0)) + 2,
        "title": question[:120] if int(conversation.get("message_count", 0)) == 0 else conversation["title"],
        "mode": body.mode,
    })
    return {"conversation_id": conversation_id, "answer": result, "messages": [
        _message_out(user_message), _message_out(assistant_message),
    ]}


@router.post("/{conversation_id}/messages/stream", dependencies=[Depends(auth.require_csrf)])
def stream_message(conversation_id: str, body: MessageCreate, request: Request,
                   user=Depends(auth.get_current_user)):
    repository = _repo(request)
    query = {"_id": conversation_id, "owner_id": user["id"]}
    conversation = repository.find_one("conversations", query)
    if not conversation:
        logger.warning("ai_chat_request conversation_id=%s result=conversation_not_found", conversation_id)
        raise HTTPException(404, "Conversation not found")
    question = body.question.strip()
    model_for_log = body.model or ai_engine._configured_local_model()
    logger.info("ai_chat_request conversation_id=%s mode=%s model=%s document_count=%d",
                conversation_id, body.mode, model_for_log or "unconfigured",
                len(conversation.get("document_ids") or ([conversation.get("document_id")] if conversation.get("document_id") else [])))

    if body.request_id:
        request_query = {"owner_id": user["id"], "conversation_id": conversation_id,
                         "request_id": body.request_id}
        prior_rows = repository.find_many("messages", request_query, "created_at")
        prior_user = next((row for row in prior_rows if row.get("role") == "user"), None)
        prior_assistant = next((row for row in prior_rows if row.get("role") == "assistant"), None)
        if prior_user and prior_user.get("text") != question:
            raise HTTPException(409, "This retry key is already associated with a different question.")
        if prior_assistant:
            prior_document_ids = conversation.get("document_ids") or ([conversation.get("document_id")] if conversation.get("document_id") else [])
            result = {"answer": prior_assistant.get("text", ""),
                      "found": bool(prior_assistant.get("sources") or not prior_document_ids),
                      "mode": prior_assistant.get("mode", "local"),
                      "provider": prior_assistant.get("provider", "ollama"),
                      "model": prior_assistant.get("model"),
                      "sources": prior_assistant.get("sources", []),
                      "web_research_used": prior_assistant.get("web_research_used", False)}

            def replay():
                logger.info("ai_stream_replay conversation_id=%s request_id=%s", conversation_id, body.request_id)
                yield "event: done\ndata: " + json.dumps({
                    "conversation_id": conversation_id, "answer": result,
                    "messages": [_message_out(row) for row in prior_rows if row.get("role") in {"user", "assistant"}],
                }, ensure_ascii=False) + "\n\n"

            return StreamingResponse(replay(), media_type="text/event-stream", headers={
                "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
            })

    document_ids = conversation.get("document_ids") or ([conversation["document_id"]] if conversation.get("document_id") else [])
    documents = _load_documents(document_ids, user["id"])
    try:
        retrieved = _retrieved(question, documents, user["id"])
        logger.info("ai_retrieval conversation_id=%s result=%s source_count=%d",
                    conversation_id, "evidence" if retrieved["sources"] else "no_relevant_evidence",
                    len(retrieved["sources"]))
    except Exception:
        logger.exception("ai_retrieval_failed conversation_id=%s", conversation_id)
        retrieved = {"found": False, "answer": "No supporting document passage was retrieved.",
                     "sources": [], "mode": "extractive-fallback"}
    history = _history(repository, user["id"], conversation_id)
    fallback = False
    metadata = None
    tokens = None
    final_text = ""
    try:
        metadata, tokens = ai_engine.stream_answer(
            question, history, retrieved["sources"], body.mode, body.allow_hosted,
            body.allow_hosted_documents, body.web_research,
            document_context_selected=bool(documents),
            requested_model=body.model,
            document_filenames=[document.get("filename", "Document") for document in documents],
            document_types=[document.get("doc_type", "unknown") for document in documents],
        )
    except ai_engine.EngineError as exc:
        logger.warning("ai_stream_setup_failed conversation_id=%s mode=%s model=%s error_type=%s",
                       conversation_id, body.mode, model_for_log or "unconfigured", type(exc).__name__)
        if not documents or not retrieved["sources"]:
            raise HTTPException(exc.status_code, str(exc)) from exc
        fallback = True
        final_text = retrieved["answer"]

    logger.info("ai_stream_started conversation_id=%s mode=%s model=%s fallback=%s",
                conversation_id, body.mode, model_for_log or "unconfigured", fallback)

    def events():
        nonlocal final_text
        saved = False
        first_token_logged = False
        try:
            if fallback:
                yield "event: delta\ndata: " + json.dumps({"text": final_text}, ensure_ascii=False) + "\n\n"
            else:
                pieces = []
                for token in tokens:
                    pieces.append(token)
                    if not first_token_logged:
                        first_token_logged = True
                        logger.info("ai_stream_first_token conversation_id=%s", conversation_id)
                    yield "event: delta\ndata: " + json.dumps({"text": token}, ensure_ascii=False) + "\n\n"
                final_text = ai_engine.validate_citations("".join(pieces), metadata["sources"])
                if not final_text.strip():
                    raise ai_engine.EngineError("The configured model returned an empty response. Try again or choose another model.")
                if metadata["web_research_note"] and body.web_research:
                    final_text += f"\n\n(Web research note: {metadata['web_research_note']})"
            now = _now()
            user_message = {
                "_id": uuid.uuid4().hex, "owner_id": user["id"], "conversation_id": conversation_id,
                "role": "user", "text": question, "sources": [], "created_at": now,
                **({"request_id": body.request_id} if body.request_id else {}),
            }
            assistant_message = {
                "_id": uuid.uuid4().hex, "owner_id": user["id"], "conversation_id": conversation_id,
                "role": "assistant", "text": final_text,
                "sources": retrieved["sources"] if fallback else metadata["sources"], "created_at": _now(),
                "provider": "extractive" if fallback else metadata["provider"],
                "model": None if fallback else metadata["model"],
                "mode": retrieved.get("mode", "extractive-fallback") if fallback else metadata["mode"],
                "web_research_used": False if fallback else metadata["web_research_used"],
                **({"request_id": body.request_id} if body.request_id else {}),
            }
            if body.request_id:
                user_message, _ = repository.insert_message_once(user_message)
                if user_message.get("text") != question:
                    raise ai_engine.EngineError("This retry key is already associated with a different question.", 409)
                assistant_message, assistant_created = repository.insert_message_once(assistant_message)
            else:
                repository.insert_one("messages", user_message)
                repository.insert_one("messages", assistant_message)
                assistant_created = True
            previous_count = int(conversation.get("message_count", 0))
            if assistant_created:
                repository.update_one("conversations", query, {
                    "updated_at": assistant_message["created_at"],
                    "message_count": previous_count + 2,
                    "title": question[:120] if previous_count == 0 else conversation["title"], "mode": body.mode,
                })
            final_text = assistant_message.get("text", final_text)
            result = {"answer": final_text, "found": bool(assistant_message["sources"] or not documents),
                      "mode": assistant_message["mode"], "provider": assistant_message["provider"],
                      "model": assistant_message["model"], "sources": assistant_message["sources"],
                      "web_research_used": assistant_message["web_research_used"]}
            yield "event: done\ndata: " + json.dumps({
                "conversation_id": conversation_id, "answer": result,
                "messages": [_message_out(user_message), _message_out(assistant_message)],
            }, ensure_ascii=False) + "\n\n"
            saved = True
            logger.info("ai_stream_completed conversation_id=%s model=%s source_count=%d",
                        conversation_id, assistant_message.get("model") or "extractive",
                        len(assistant_message.get("sources", [])))
        except ai_engine.EngineError as exc:
            logger.warning("ai_stream_failed conversation_id=%s error_type=%s status=%d",
                           conversation_id, type(exc).__name__, exc.status_code)
            yield "event: error\ndata: " + json.dumps({"message": str(exc)}, ensure_ascii=False) + "\n\n"
        except Exception:
            logger.exception("ai_stream_unhandled_exception conversation_id=%s mode=%s model=%s",
                             conversation_id, body.mode, model_for_log or "unconfigured")
            yield "event: error\ndata: " + json.dumps({
                "message": "The chat stream was interrupted by a server error. Retry this message.",
            }, ensure_ascii=False) + "\n\n"
        finally:
            if not saved:
                logger.info("ai_stream_closed_without_save conversation_id=%s", conversation_id)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })


@router.delete("/{conversation_id}", status_code=204, dependencies=[Depends(auth.require_csrf)])
def delete_conversation(conversation_id: str, request: Request, user=Depends(auth.get_current_user)):
    repository = _repo(request)
    query = {"_id": conversation_id, "owner_id": user["id"]}
    if not repository.find_one("conversations", query):
        raise HTTPException(404, "Conversation not found")
    repository.delete_many("messages", {"owner_id": user["id"], "conversation_id": conversation_id})
    repository.delete_one("conversations", query)


def delete_for_document(repository, owner_id: str, document_id: str):
    conversations = repository.find_many("conversations", {
        "owner_id": owner_id,
        "$or": [{"document_id": document_id}, {"document_ids": document_id}],
    })
    for conversation in conversations:
        conversation_id = conversation.get("_id", conversation.get("id"))
        repository.delete_many("messages", {"owner_id": owner_id, "conversation_id": conversation_id})
        repository.delete_one("conversations", {"_id": conversation_id, "owner_id": owner_id})


def export_owner_conversations(repository, owner_id: str):
    exported = []
    for row in repository.find_many("conversations", {"owner_id": owner_id}, "created_at", True):
        conversation = _conversation_out(row)
        conversation["messages"] = [
            _message_out(message) for message in repository.find_many(
                "messages", {"owner_id": owner_id, "conversation_id": conversation["id"]}, "created_at"
            )
        ]
        exported.append(conversation)
    return exported
