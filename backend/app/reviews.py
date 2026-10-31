"""Owner-requested field reviews with explicit, assignment-only staff access."""
from datetime import datetime, timezone
import os
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import auth, store
from .auth_store import AuthRepository
from .security import normalize_email


router = APIRouter(prefix="/api/v1", tags=["reviews"])
STAFF_ROLES = {"document_reviewer", "bank_employee", "loan_officer", "branch_manager", "auditor"}
OPEN_STATUSES = {"submitted", "assigned", "needs_owner_action", "owner_responded"}


def _repo(request):
    return AuthRepository(db=getattr(request.app.state, "mongo_database", None), root=store.ROOT)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _require(user, *roles):
    if user.get("role") not in roles:
        raise HTTPException(403, "This action is not available for your account role.")


def _audit(repo, owner_id, actor_id, action, target_id, metadata=None):
    repo.insert_one("audit_events", {
        "_id": uuid.uuid4().hex, "owner_id": owner_id, "actor_id": actor_id,
        "action": action, "target_type": "review_task", "target_id": target_id,
        "metadata": metadata or {}, "created_at": _now(),
    })


def _out(task, include_evidence=False):
    result = {key: value for key, value in task.items() if key not in {"_id", "owner_id", "snapshot"}}
    result["id"] = task.get("_id", task.get("id"))
    if include_evidence:
        result["snapshot"] = task.get("snapshot", {})
    return result


class ReviewRequestIn(BaseModel):
    document_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    field_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(default="", max_length=500)


class AssignmentIn(BaseModel):
    reviewer_email: str = Field(min_length=3, max_length=254)


class DecisionIn(BaseModel):
    decision: Literal["approved", "needs_owner_action", "rejected"]
    comment: str = Field(default="", max_length=1000)


class OwnerResponseIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class RoleChangeIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["customer", "document_reviewer", "bank_employee", "loan_officer", "branch_manager", "auditor"]


@router.post("/reviews", status_code=201, dependencies=[Depends(auth.require_csrf)])
def request_review(body: ReviewRequestIn, request: Request, user=Depends(auth.get_current_user)):
    repository = _repo(request)
    try:
        document = store.load(body.document_id, user["id"])
    except KeyError as exc:
        raise HTTPException(404, "Document not found") from exc
    if str(document.get("doc_type", "")).startswith("bank_statement_"):
        match = re.fullmatch(r"transaction:(\d{1,9})", body.field_id)
        if not match:
            raise HTTPException(422, "Choose a supported statement transaction row for review.")
        row_number = int(match.group(1))
        try:
            row = next((item for item in store.all_transactions(body.document_id, user["id"])
                        if item.get("row_number") == row_number), None)
        except (KeyError, ValueError) as exc:
            raise HTTPException(404, "Statement transaction row not found") from exc
        if row is None:
            raise HTTPException(404, "Statement transaction row not found")
        effective = row.get("effective") or row
        snapshot = {
            "kind": "statement_transaction", "filename": document.get("filename", "Statement"),
            "document_type": document.get("doc_type"), "field_id": body.field_id,
            "row_number": row_number,
            "source_sheet": row.get("source_sheet"),
            "page": row.get("page"),
            "source_values": {key: row.get(key) for key in (
                "date", "description", "amount_minor", "debit_minor", "credit_minor",
                "balance_minor", "currency", "source_values", "evidence", "page", "source_sheet",
            )},
            "effective_values": {key: effective.get(key) for key in (
                "date", "description", "amount_minor", "debit_minor", "credit_minor",
                "balance_minor", "currency",
            )},
            "correction_revision": int(row.get("correction_revision", 0)),
            "user_correction": row.get("user_correction"),
        }
    else:
        if body.field_id.startswith("transaction:"):
            raise HTTPException(404, "Statement transaction row not found")
        term = document.get("terms", {}).get(body.field_id)
        if term is None:
            raise HTTPException(404, "Extracted field not found")
        snapshot = {
            "filename": document.get("filename", "Document"),
            "document_type": document.get("doc_type"),
            "field_id": body.field_id,
            "value": term.get("value"),
            "effective_value": term.get("effective_value", term.get("value")),
            "user_correction": term.get("user_correction"),
            "raw": term.get("raw"), "page": term.get("page"),
            "evidence": term.get("evidence"), "status": term.get("status"),
            "confidence": term.get("confidence"),
        }
    existing = repository.find_many("review_tasks", {
        "owner_id": user["id"], "document_id": body.document_id, "field_id": body.field_id,
    })
    active = next((task for task in existing if task.get("status") in OPEN_STATUSES), None)
    if active:
        return {**_out(active), "created": False}

    now, task_id = _now(), uuid.uuid4().hex
    task = {
        "_id": task_id, "owner_id": user["id"], "requested_by": user["id"],
        "document_id": body.document_id, "field_id": body.field_id,
        "reason": body.reason.strip(), "snapshot": snapshot,
        "status": "submitted", "assigned_to": None, "created_at": now,
        "updated_at": now, "history": [{"action": "submitted", "actor_id": user["id"], "comment": body.reason.strip(), "created_at": now}],
    }
    repository.insert_one("review_tasks", task)
    _audit(repository, user["id"], user["id"], "review_requested", task_id, {"field_id": body.field_id})
    return {**_out(task), "created": True}


@router.get("/reviews")
def list_reviews(request: Request, document_id: str | None = Query(default=None, pattern=r"^[0-9a-f]{32}$"),
                 offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                 user=Depends(auth.get_current_user)):
    repository = _repo(request)
    role = user.get("role", "customer")
    if role == "system_admin":
        rows = repository.find_many("review_tasks", sort_field="created_at", descending=True)
    elif role == "customer":
        rows = repository.find_many("review_tasks", {"owner_id": user["id"]}, "created_at", True)
    else:
        rows = repository.find_many("review_tasks", {"assigned_to": user["id"]}, "created_at", True)
    if document_id:
        rows = [row for row in rows if row.get("document_id") == document_id]
    total = len(rows)
    rows = rows[offset:offset + limit]
    return {"reviews": [_out(row, include_evidence=role not in {"system_admin", "customer"}) for row in rows],
            "total": total, "offset": offset, "limit": limit}


@router.post("/reviews/{task_id}/assign", dependencies=[Depends(auth.require_csrf)])
def assign_review(task_id: str, body: AssignmentIn, request: Request, user=Depends(auth.get_current_user)):
    _require(user, "system_admin")
    repository = _repo(request)
    email = normalize_email(body.reviewer_email)
    reviewer = repository.find_one("users", {"email_normalized": email})
    if not reviewer or not reviewer.get("email_verified") or reviewer.get("role", "customer") not in STAFF_ROLES:
        raise HTTPException(422, "Choose a verified staff account with an assigned review role.")
    task = repository.find_one("review_tasks", {"_id": task_id})
    if not task or task.get("status") not in {"submitted", "assigned", "owner_responded"}:
        raise HTTPException(404, "Review task is unavailable for assignment.")
    now = _now()
    history = list(task.get("history", [])) + [{"action": "assigned", "actor_id": user["id"],
                                                  "assigned_to": reviewer["_id"], "created_at": now}]
    updated = repository.update_one("review_tasks", {"_id": task_id, "status": task["status"]},
                                    {"assigned_to": reviewer["_id"], "status": "assigned",
                                     "updated_at": now, "history": history})
    if not updated:
        raise HTTPException(409, "Review task changed. Refresh and try again.")
    _audit(repository, task["owner_id"], user["id"], "review_assigned", task_id,
           {"assigned_to": reviewer["_id"]})
    return {"id": task_id, "status": "assigned", "assigned_to": reviewer["_id"]}


@router.post("/reviews/{task_id}/decision", dependencies=[Depends(auth.require_csrf)])
def decide_review(task_id: str, body: DecisionIn, request: Request, user=Depends(auth.get_current_user)):
    if user.get("role") not in STAFF_ROLES:
        raise HTTPException(403, "A staff review role is required.")
    repository = _repo(request)
    task = repository.find_one("review_tasks", {"_id": task_id, "assigned_to": user["id"], "status": "assigned"})
    if not task:
        raise HTTPException(404, "Assigned review task not found")
    now = _now()
    entry = {"action": body.decision, "actor_id": user["id"], "comment": body.comment.strip(), "created_at": now}
    history = list(task.get("history", [])) + [entry]
    changed = repository.update_one("review_tasks", {"_id": task_id, "assigned_to": user["id"], "status": "assigned"},
                                   {"status": body.decision, "updated_at": now, "history": history})
    if not changed:
        raise HTTPException(409, "Review task changed. Refresh and try again.")
    _audit(repository, task["owner_id"], user["id"], "review_decided", task_id, {"status": body.decision})
    return {"id": task_id, "status": body.decision}


@router.post("/reviews/{task_id}/response", dependencies=[Depends(auth.require_csrf)])
def owner_response(task_id: str, body: OwnerResponseIn, request: Request, user=Depends(auth.get_current_user)):
    repository = _repo(request)
    task = repository.find_one("review_tasks", {"_id": task_id, "owner_id": user["id"], "status": "needs_owner_action"})
    if not task:
        raise HTTPException(404, "Review task is not awaiting your response")
    now = _now()
    history = list(task.get("history", [])) + [{"action": "owner_responded", "actor_id": user["id"],
                                                 "comment": body.message.strip(), "created_at": now}]
    if not repository.update_one("review_tasks", {"_id": task_id, "owner_id": user["id"], "status": "needs_owner_action"},
                                 {"status": "owner_responded", "updated_at": now, "history": history}):
        raise HTTPException(409, "Review task changed. Refresh and try again.")
    _audit(repository, user["id"], user["id"], "review_owner_responded", task_id)
    return {"id": task_id, "status": "owner_responded"}


@router.get("/admin/users")
def list_staff_users(request: Request, user=Depends(auth.get_current_user)):
    _require(user, "system_admin")
    rows = _repo(request).find_many("users")
    result = []
    for row in rows:
        if row.get("deleted_at"):
            continue
        safe = auth._safe_user(row)
        result.append(safe)
    return {"users": result}


@router.patch("/admin/users/role", dependencies=[Depends(auth.require_csrf)])
def change_role(body: RoleChangeIn, request: Request, user=Depends(auth.get_current_user)):
    _require(user, "system_admin")
    repository = _repo(request)
    target = repository.find_one("users", {"email_normalized": normalize_email(body.email)})
    if not target or target.get("deleted_at"):
        raise HTTPException(404, "Account not found")
    if normalize_email(target.get("email", "")) in {
        normalize_email(value) for value in os.getenv("BANKEASE_ADMIN_EMAILS", "").split(",") if value.strip()
    }:
        raise HTTPException(409, "Bootstrap administrators are managed through server configuration.")
    old_role = target.get("role", "customer")
    now = _now()
    repository.update_one("users", {"_id": target["_id"]}, {"role": body.role, "updated_at": datetime.now(timezone.utc)})
    if body.role == "customer" and old_role in STAFF_ROLES:
        for task in repository.find_many("review_tasks", {"assigned_to": target["_id"]}):
            if task.get("status") == "assigned":
                history = list(task.get("history", [])) + [{"action": "unassigned_role_changed", "actor_id": user["id"], "created_at": now}]
                repository.update_one("review_tasks", {"_id": task["_id"], "assigned_to": target["_id"], "status": "assigned"},
                                      {"assigned_to": None, "status": "submitted", "updated_at": now, "history": history})
    _audit(repository, user["id"], user["id"], "user_role_changed", target["_id"], {"from": old_role, "to": body.role})
    updated = {**target, "role": body.role}
    return {"user": auth._safe_user(updated)}


@router.get("/audit-events")
def audit_events(request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                 user=Depends(auth.get_current_user)):
    _require(user, "system_admin")
    rows = _repo(request).find_many("audit_events", sort_field="created_at", descending=True)
    rows = rows[offset:offset + limit]
    return {"events": [{key: value for key, value in row.items() if key != "_id"} | {"id": row.get("_id")} for row in rows],
            "offset": offset, "limit": limit}
