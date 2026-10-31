"""Account and opaque-cookie session API."""
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import ipaddress
import os
import re
import threading
import uuid
import json
import zipfile
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import store
from .auth_store import AuthRepository, DuplicateEmailError
from .config import load_settings
from .security import (
    constant_time_equal,
    hash_password,
    new_secret,
    normalize_email,
    password_needs_rehash,
    token_hash,
    validate_password,
    verify_password,
)


router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
SESSION_COOKIE = "bankease_session"
CSRF_COOKIE = "bankease_csrf"
SESSION_SECONDS = 12 * 60 * 60
VERIFY_SECONDS = 24 * 60 * 60
RESET_SECONDS = 30 * 60
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_ACCOUNT_EXPORT_BYTES = 100 * 1024 * 1024


class RateLimiter:
    """Per-process development limiter; production remains disabled until distributed limits exist."""
    def __init__(self, limit=12, window_seconds=60):
        self.limit = limit
        self.window_seconds = window_seconds
        self.events = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, request, action):
        host = request.client.host if request.client else "unknown"
        key = (host, action)
        now = datetime.now(timezone.utc).timestamp()
        with self.lock:
            events = self.events[key]
            while events and now - events[0] >= self.window_seconds:
                events.popleft()
            if len(events) >= self.limit:
                raise HTTPException(429, "Too many attempts. Please wait and try again.")
            events.append(now)


_LIMITER = RateLimiter()


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    password_confirmation: str = Field(min_length=12, max_length=128)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class ResetIn(TokenIn):
    password: str = Field(min_length=12, max_length=128)
    password_confirmation: str = Field(min_length=12, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    password_confirmation: str = Field(min_length=12, max_length=128)


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _repo(request):
    database = getattr(request.app.state, "mongo_database", None)
    return AuthRepository(db=database, root=store.ROOT)


def _settings(request):
    configured = getattr(request.app.state, "settings", None)
    return configured if configured is not None else load_settings()


def _safe_user(user):
    configured_admins = {
        normalize_email(value) for value in os.getenv("BANKEASE_ADMIN_EMAILS", "").split(",")
        if value.strip()
    }
    role = "system_admin" if normalize_email(user.get("email", "")) in configured_admins else user.get("role", "customer")
    if role not in {"customer", "document_reviewer", "bank_employee", "loan_officer",
                    "branch_manager", "auditor", "system_admin"}:
        role = "customer"
    return {"id": user["_id"], "name": user["name"], "email": user["email"],
            "email_verified": bool(user.get("email_verified")), "role": role}


def _parse_time(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _active_session(session):
    return session and not session.get("revoked_at") and _parse_time(session["expires_at"]) > datetime.now(timezone.utc)


def _csrf_hash_valid(request, repo):
    session_token = request.cookies.get(SESSION_COOKIE)
    csrf_token = request.cookies.get(CSRF_COOKIE, "")
    if not session_token or not csrf_token:
        return True
    session = repo.find_one("sessions", {"token_hash": token_hash(session_token)})
    if not _active_session(session):
        return True
    return constant_time_equal(session.get("csrf_hash", ""), token_hash(csrf_token))


def require_csrf(request: Request):
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or not header or not constant_time_equal(cookie, header):
        raise HTTPException(403, "Invalid CSRF token")
    if not _csrf_hash_valid(request, _repo(request)):
        raise HTTPException(403, "Invalid CSRF token")


def get_current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Authentication required")
    repo = _repo(request)
    session = repo.find_one("sessions", {"token_hash": token_hash(token)})
    if not _active_session(session):
        if session:
            repo.update_one("sessions", {"_id": session["_id"]}, {"revoked_at": datetime.now(timezone.utc)})
        raise HTTPException(401, "Authentication required")
    user = repo.find_one("users", {"_id": session["owner_id"]})
    if not user or not user.get("email_verified") or user.get("deleted_at"):
        raise HTTPException(401, "Authentication required")
    request.state.auth_session = session
    return _safe_user(user)


def _set_csrf_cookie(response, request, token, max_age=SESSION_SECONDS):
    response.set_cookie(CSRF_COOKIE, token, max_age=max_age, httponly=False,
                        secure=_settings(request).environment == "production",
                        samesite="lax", path="/")


def _set_session_cookies(response, request, session_token, csrf_token):
    settings = _settings(request)
    response.set_cookie(SESSION_COOKIE, session_token, max_age=SESSION_SECONDS, httponly=True,
                        secure=settings.environment == "production", samesite="lax", path="/")
    _set_csrf_cookie(response, request, csrf_token)


def _clear_cookies(response, request):
    secure = _settings(request).environment == "production"
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, httponly=False, samesite="lax")


def _revoke_cookie_session(request, repo):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        repo.update_one("sessions", {"token_hash": token_hash(token), "revoked_at": None},
                        {"revoked_at": datetime.now(timezone.utc)})


def _issue_session(request, response, repo, owner_id):
    _revoke_cookie_session(request, repo)
    session_token, csrf_token = new_secret(), new_secret()
    now = datetime.now(timezone.utc)
    repo.insert_one("sessions", {
        "_id": uuid.uuid4().hex,
        "owner_id": owner_id,
        "token_hash": token_hash(session_token),
        "csrf_hash": token_hash(csrf_token),
        "created_at": now,
        "expires_at": now + timedelta(seconds=SESSION_SECONDS),
        "revoked_at": None,
    })
    _set_session_cookies(response, request, session_token, csrf_token)


def _development_link(request, token, action):
    settings = _settings(request)
    host = request.client.host if request.client else ""
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if settings.environment == "test" and host == "testclient":
        local = True
    if settings.environment != "development" and settings.environment != "test":
        return None
    if not local or os.getenv("DEV_EMAIL_LINKS", "true").strip().lower() != "true":
        return None
    return f"{str(request.base_url).rstrip('/')}/?{action}={token}"


@router.get("/csrf")
def csrf_token(request: Request, response: Response):
    repo = _repo(request)
    token = request.cookies.get(CSRF_COOKIE, "")
    session_token = request.cookies.get(SESSION_COOKIE)
    session = repo.find_one("sessions", {"token_hash": token_hash(session_token)}) if session_token else None
    if session and _active_session(session) and token_hash(token) == session.get("csrf_hash"):
        return {"csrf_token": token}
    token = new_secret()
    if session and _active_session(session):
        repo.update_one("sessions", {"_id": session["_id"]}, {"csrf_hash": token_hash(token)})
    _set_csrf_cookie(response, request, token)
    return {"csrf_token": token}


@router.post("/register", status_code=202, dependencies=[Depends(require_csrf)])
def register(body: RegisterIn, request: Request):
    _LIMITER.check(request, "register")
    email = normalize_email(body.email)
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(422, "Enter a valid email address")
    if body.password != body.password_confirmation:
        raise HTTPException(422, "Passwords do not match")
    try:
        password_hash = hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    user_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    user = {"_id": user_id, "name": body.name.strip(), "email": email,
            "email_normalized": email, "password_hash": password_hash,
            "email_verified": False, "role": "customer", "created_at": now, "updated_at": now}
    repo = _repo(request)
    token = new_secret()
    try:
        repo.create_user(user)
        repo.insert_one("email_verifications", {
            "_id": uuid.uuid4().hex, "owner_id": user_id, "token_hash": token_hash(token),
            "created_at": now, "expires_at": now + timedelta(seconds=VERIFY_SECONDS),
        })
    except DuplicateEmailError:
        token = ""
    except Exception:
        repo.delete_one("users", {"_id": user_id})
        raise
    link = _development_link(request, token, "verify") if token else None
    return {"message": "If this address can be registered, follow the verification instructions.",
            "delivery": "local-development-link" if link else "not-configured",
            "development_verification_url": link}


@router.post("/verify-email", dependencies=[Depends(require_csrf)])
def verify_email(body: TokenIn, request: Request):
    repo = _repo(request)
    record = repo.find_one("email_verifications", {"token_hash": token_hash(body.token)})
    if not record or _parse_time(record["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(400, "Verification link is invalid or expired")
    repo.update_one("users", {"_id": record["owner_id"], "email_verified": False},
                    {"email_verified": True, "verified_at": datetime.now(timezone.utc),
                     "updated_at": datetime.now(timezone.utc)})
    repo.delete_one("email_verifications", {"_id": record["_id"]})
    return {"message": "Email verified. You can sign in now."}


@router.post("/resend-verification", status_code=202, dependencies=[Depends(require_csrf)])
def resend_verification(body: EmailIn, request: Request):
    _LIMITER.check(request, "resend-verification")
    repo = _repo(request)
    email = normalize_email(body.email)
    user = repo.find_one("users", {"email_normalized": email, "email_verified": False})
    token = ""
    if user:
        repo.delete_many("email_verifications", {"owner_id": user["_id"]})
        token = new_secret()
        now = datetime.now(timezone.utc)
        repo.insert_one("email_verifications", {
            "_id": uuid.uuid4().hex, "owner_id": user["_id"], "token_hash": token_hash(token),
            "created_at": now, "expires_at": now + timedelta(seconds=VERIFY_SECONDS),
        })
    link = _development_link(request, token, "verify") if token else None
    return {"message": "If the account needs verification, instructions will be available.",
            "delivery": "local-development-link" if link else "not-configured",
            "development_verification_url": link}


@router.post("/login", dependencies=[Depends(require_csrf)])
def login(body: LoginIn, request: Request, response: Response):
    _LIMITER.check(request, "login")
    repo = _repo(request)
    user = repo.find_one("users", {"email_normalized": normalize_email(body.email)})
    if not user or not verify_password(body.password, user.get("password_hash")) or not user.get("email_verified") or user.get("deleted_at"):
        raise HTTPException(401, "Invalid email or password")
    if password_needs_rehash(user["password_hash"]):
        repo.update_one("users", {"_id": user["_id"]},
                        {"password_hash": hash_password(body.password), "updated_at": datetime.now(timezone.utc)})
    _issue_session(request, response, repo, user["_id"])
    return {"user": _safe_user(user)}


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
def logout(request: Request, response: Response):
    repo = _repo(request)
    _revoke_cookie_session(request, repo)
    _clear_cookies(response, request)


@router.get("/me")
def me(user=Depends(get_current_user)):
    return {"user": user}


@router.patch("/me", dependencies=[Depends(require_csrf)])
def update_profile(body: ProfileIn, request: Request, user=Depends(get_current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Name cannot be empty")
    _repo(request).update_one("users", {"_id": user["id"]},
                              {"name": name, "updated_at": datetime.now(timezone.utc)})
    return {"user": {**user, "name": name}}


@router.post("/forgot-password", status_code=202, dependencies=[Depends(require_csrf)])
def forgot_password(body: EmailIn, request: Request):
    _LIMITER.check(request, "forgot-password")
    repo = _repo(request)
    email = normalize_email(body.email)
    user = repo.find_one("users", {"email_normalized": email, "email_verified": True})
    token = ""
    if user:
        repo.delete_many("password_resets", {"owner_id": user["_id"]})
        token = new_secret()
        now = datetime.now(timezone.utc)
        repo.insert_one("password_resets", {
            "_id": uuid.uuid4().hex, "owner_id": user["_id"], "token_hash": token_hash(token),
            "created_at": now, "expires_at": now + timedelta(seconds=RESET_SECONDS),
        })
    link = _development_link(request, token, "reset") if token else None
    return {"message": "If the account exists, password reset instructions will be available.",
            "delivery": "local-development-link" if link else "not-configured",
            "development_reset_url": link}


def _replace_password(repo, user_id, password):
    try:
        encoded = hash_password(password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    now = datetime.now(timezone.utc)
    repo.update_one("users", {"_id": user_id}, {"password_hash": encoded, "updated_at": now})
    repo.delete_many("sessions", {"owner_id": user_id})


@router.post("/reset-password", dependencies=[Depends(require_csrf)])
def reset_password(body: ResetIn, request: Request):
    _LIMITER.check(request, "reset-password")
    if body.password != body.password_confirmation:
        raise HTTPException(422, "Passwords do not match")
    repo = _repo(request)
    record = repo.find_one("password_resets", {"token_hash": token_hash(body.token)})
    if not record or _parse_time(record["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(400, "Password reset link is invalid or expired")
    _replace_password(repo, record["owner_id"], body.password)
    repo.delete_one("password_resets", {"_id": record["_id"]})
    return {"message": "Password updated. Sign in with your new password."}


@router.post("/change-password", dependencies=[Depends(require_csrf)])
def change_password(body: ChangePasswordIn, request: Request, response: Response,
                    user=Depends(get_current_user)):
    if body.new_password != body.password_confirmation:
        raise HTTPException(422, "Passwords do not match")
    repo = _repo(request)
    record = repo.find_one("users", {"_id": user["id"]})
    if not record or not verify_password(body.current_password, record.get("password_hash")):
        raise HTTPException(400, "Current password is incorrect")
    _replace_password(repo, user["id"], body.new_password)
    _issue_session(request, response, repo, user["id"])
    return {"message": "Password updated"}


@router.get("/sessions")
def sessions(request: Request, user=Depends(get_current_user)):
    repo = _repo(request)
    current = request.state.auth_session["_id"]
    rows = repo.find_many("sessions", {"owner_id": user["id"], "revoked_at": None}, "created_at", True)
    active = [row for row in rows if _active_session(row)]
    return {"sessions": [{"id": row["_id"], "created_at": row["created_at"],
                           "expires_at": row["expires_at"], "current": row["_id"] == current}
                          for row in active]}


@router.delete("/sessions/{session_id}", status_code=204, dependencies=[Depends(require_csrf)])
def revoke_session(session_id: str, request: Request, user=Depends(get_current_user)):
    repo = _repo(request)
    if not re.fullmatch(r"[0-9a-f]{32}", session_id):
        raise HTTPException(404, "Session not found")
    changed = repo.update_one("sessions", {"_id": session_id, "owner_id": user["id"], "revoked_at": None},
                              {"revoked_at": datetime.now(timezone.utc)})
    if not changed:
        raise HTTPException(404, "Session not found")


@router.get("/export")
def export_user_data(request: Request, user=Depends(get_current_user)):
    repo = _repo(request)
    from .conversations import export_owner_conversations
    preferences = repo.find_one("user_preferences", {"owner_id": user["id"]})
    documents = store.list_owner(user["id"])
    for document in documents:
        document.pop("transactions", None)
    return {"user": user, "documents": documents,
            "transactions": store.export_transactions(user["id"]),
            "comparisons": store.export_comparisons(user["id"]),
            "conversations": export_owner_conversations(repo, user["id"]),
            "review_tasks": repo.find_many("review_tasks", {"owner_id": user["id"]}, "created_at", True),
            "preferences": preferences or {"owner_id": user["id"]}}


@router.get("/export/archive")
def export_user_archive(request: Request, user=Depends(get_current_user)):
    """Download owner-scoped analysis data and originals in a bounded ZIP archive."""
    payload = export_user_data(request, user)
    original_files = []
    manifest_rows = []
    total_bytes = 0
    for document in payload["documents"]:
        reference = document.pop("source_file", None)
        archive_name = None
        included = False
        if reference:
            extension = reference.get("extension")
            if extension not in {"pdf", "csv"}:
                raise HTTPException(500, "A stored source reference is invalid.")
            try:
                path, _ = store.source_path(document["id"], user["id"])
            except FileNotFoundError:
                path = None
            if path is not None:
                file_bytes = path.read_bytes()
                total_bytes += len(file_bytes)
                if total_bytes > MAX_ACCOUNT_EXPORT_BYTES:
                    raise HTTPException(413, "Account export exceeds the 100 MB uncompressed size limit.")
                archive_name = f"documents/{document['id']}.{extension}"
                original_files.append((archive_name, file_bytes))
                included = True
        document["source_available"] = included
        manifest_rows.append({
            "document_id": document["id"], "filename": document.get("filename"),
            "source_included": included, "archive_path": archive_name,
        })
    account_bytes = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    manifest_bytes = json.dumps({
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "documents": manifest_rows,
    }, ensure_ascii=False, indent=2).encode("utf-8")
    total_bytes += len(account_bytes) + len(manifest_bytes)
    if total_bytes > MAX_ACCOUNT_EXPORT_BYTES:
        raise HTTPException(413, "Account export exceeds the 100 MB uncompressed size limit.")
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("account.json", account_bytes)
        bundle.writestr("manifest.json", manifest_bytes)
        for name, content in original_files:
            bundle.writestr(name, content)
    return Response(content=archive.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="bankease-account-export.zip"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })


class DeleteAccountIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=1, max_length=30)


@router.delete("/me", status_code=204, dependencies=[Depends(require_csrf)])
def delete_account(body: DeleteAccountIn, request: Request, response: Response,
                   user=Depends(get_current_user)):
    if body.confirmation != "DELETE":
        raise HTTPException(422, "Type DELETE to confirm account deletion")
    repo = _repo(request)
    record = repo.find_one("users", {"_id": user["id"]})
    if not record or not verify_password(body.password, record.get("password_hash")):
        raise HTTPException(400, "Password is incorrect")
    repo.delete_many("processing_jobs", {"owner_id": user["id"]})
    store.delete_owner(user["id"])
    for task in repo.find_many("review_tasks", {"assigned_to": user["id"]}):
        repo.update_one("review_tasks", {"_id": task["_id"], "assigned_to": user["id"]},
                        {"assigned_to": None, "status": "submitted", "updated_at": datetime.now(timezone.utc)})
    for collection in ("sessions", "email_verifications", "password_resets", "user_preferences",
                       "conversations", "messages",
                       "review_tasks", "audit_events"):
        repo.delete_many(collection, {"owner_id": user["id"]})
    repo.delete_many("audit_events", {"actor_id": user["id"]})
    repo.delete_one("users", {"_id": user["id"]})
    _clear_cookies(response, request)
