"""Account workflows and per-user API isolation."""
import pathlib
import sys
import json
from io import BytesIO
from urllib.parse import parse_qs, urlparse
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from backend.app import ai_engine, auth, conversations, store
from backend.app.auth_store import AuthRepository
from backend.app.main import app
from backend.app import jobs
from make_synthetic import make


def test_general_conversation_streams_and_persists_without_document_context(client, monkeypatch):
    create_account(client, "general-chat@example.test")
    sign_in(client, "general-chat@example.test")
    monkeypatch.setattr(ai_engine, "_ollama_models", lambda timeout=2.0: (True, [{"name": "qwen3:4b"}]))
    monkeypatch.setattr(ai_engine, "stream_answer", lambda question, history, sources, *args, **kwargs: ({
        "provider": "ollama", "model": "qwen3:4b", "mode": "local",
        "web_research_requested": False, "web_research_used": False,
        "web_research_note": None, "sources": [],
    }, iter(["Compound interest", " adds interest over time."])))

    created = post_csrf(client, "/api/v1/conversations", {
        "document_ids": [], "title": "General question", "mode": "local",
    })
    assert created.status_code == 201, created.text
    conversation = created.json()
    assert conversation["document_ids"] == []
    assert conversation["document_id"] is None
    assert conversation["document_filename"] == "General conversation"

    streamed = post_csrf(client, f"/api/v1/conversations/{conversation['id']}/messages/stream", {
        "question": "What is compound interest?", "mode": "local",
    })
    assert streamed.status_code == 200, streamed.text
    assert "event: delta" in streamed.text
    assert "event: done" in streamed.text
    history = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()
    assert [message["role"] for message in history["messages"]] == ["user", "assistant"]
    assert history["messages"][-1]["text"] == "Compound interest adds interest over time."
    assert history["messages"][-1]["provider"] == "ollama"


def test_conversation_create_validates_and_normalizes_titles(client):
    create_account(client, "conversation-title@example.test")
    sign_in(client, "conversation-title@example.test")

    short = post_csrf(client, "/api/v1/conversations", {"title": "  Loan   review  "})
    assert short.status_code == 201, short.text
    assert short.json()["title"] == "Loan review"

    exact_limit_title = "x" * 1000
    exact_limit = post_csrf(client, "/api/v1/conversations", {"title": exact_limit_title})
    assert exact_limit.status_code == 201, exact_limit.text
    assert exact_limit.json()["title"] == exact_limit_title

    oversized = post_csrf(client, "/api/v1/conversations", {"title": "x" * 1001})
    assert oversized.status_code == 422
    assert "Conversation title must be 1000 characters or fewer." in oversized.text

    for empty_title in ("", "   \t\n  "):
        empty = post_csrf(client, "/api/v1/conversations", {"title": empty_title})
        assert empty.status_code == 422
        assert "Conversation title cannot be empty." in empty.text

    default_title = post_csrf(client, "/api/v1/conversations", {})
    assert default_title.status_code == 201, default_title.text
    assert default_title.json()["title"] == "New conversation"


def test_conversation_can_be_renamed_pinned_and_updated_with_documents(client, monkeypatch):
    create_account(client, "conversation-management@example.test")
    sign_in(client, "conversation-management@example.test")
    document_id = "b" * 32
    monkeypatch.setattr(store, "load", lambda requested_id, owner_id: {
        "id": requested_id, "owner_id": owner_id, "filename": "terms.docx", "status": "needs_review",
        "doc_type": "unknown", "source_format": "docx", "source_locator_type": "section",
        "pages": ["The agreement requires monthly repayment."],
    })
    created = post_csrf(client, "/api/v1/conversations", {"title": "First title"})
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    updated = post_csrf(client, f"/api/v1/conversations/{conversation_id}", {
        "title": "  Review   agreement  ", "is_pinned": True, "document_ids": [document_id],
    }, method="PATCH")
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "Review agreement"
    assert updated.json()["is_pinned"] is True
    assert updated.json()["document_ids"] == [document_id]
    assert updated.json()["document_filenames"] == ["terms.docx"]

    unpinned = post_csrf(client, f"/api/v1/conversations/{conversation_id}", {"is_pinned": False}, method="PATCH")
    assert unpinned.status_code == 200, unpinned.text
    assert unpinned.json()["is_pinned"] is False
    assert client.get("/api/v1/conversations").json()["conversations"][0]["title"] == "Review agreement"


def test_automatic_conversation_title_generation_caps_at_1000_characters():
    question = "What is inflation and how might it affect a floating-rate loan? " + "Explain this clearly. " * 60
    generated_title = conversations.conversation_title_from_question(question)

    assert len(generated_title) <= 1000
    assert generated_title.endswith("...")
    assert generated_title[:-3].rstrip()[-1].isalnum() or generated_title[:-3].rstrip()[-1] in ".?!"


def test_long_question_gets_bounded_title_and_is_saved_in_full(client, monkeypatch):
    create_account(client, "long-question-title@example.test")
    sign_in(client, "long-question-title@example.test")
    question = (
        "What is inflation, and how could it affect the cost of a floating-rate loan? "
        + "Please explain the relationship in simple language. " * 6
    ).strip()
    generated_title = conversations.conversation_title_from_question(question)
    assert 1000 >= len(generated_title) > 0
    assert generated_title == question

    received_questions = []

    def stream_answer(received_question, *args, **kwargs):
        received_questions.append(received_question)
        return ({"provider": "ollama", "model": "qwen3:4b", "mode": "local",
                 "web_research_requested": False, "web_research_used": False,
                 "web_research_note": None, "sources": []}, iter(["Inflation is a general rise in prices."]))

    monkeypatch.setattr(ai_engine, "stream_answer", stream_answer)
    created = post_csrf(client, "/api/v1/conversations", {
        "document_ids": [], "title": generated_title, "mode": "local",
    })
    assert created.status_code == 201, created.text

    streamed = post_csrf(client, f"/api/v1/conversations/{created.json()['id']}/messages/stream", {
        "question": question, "mode": "local",
    })
    assert streamed.status_code == 200, streamed.text
    assert "event: done" in streamed.text
    assert received_questions == [question]
    history = client.get(f"/api/v1/conversations/{created.json()['id']}/messages").json()
    assert history["messages"][0]["text"] == question
    assert history["conversation"]["title"] == generated_title


def test_long_document_filename_does_not_change_conversation_title_limit(client, monkeypatch):
    create_account(client, "long-document-name@example.test")
    sign_in(client, "long-document-name@example.test")
    document_id = "a" * 32
    filename = "loan-agreement-" + "very-long-document-name-" * 7 + ".pdf"
    monkeypatch.setattr(store, "load", lambda requested_id, owner_id: {
        "id": document_id, "filename": filename, "doc_type": "loan_agreement",
        "pages": [{"page": 1, "text": "Loan agreement terms"}], "status": "completed",
    })

    created = post_csrf(client, "/api/v1/conversations", {
        "document_ids": [document_id], "title": "Review loan agreement",
    })
    assert created.status_code == 201, created.text
    assert created.json()["title"] == "Review loan agreement"
    assert created.json()["document_filename"] == filename
    assert len(created.json()["document_filename"]) > 120


def test_ai_status_requires_authenticated_user(client):
    assert client.get("/api/v1/ai/status").status_code == 401
    assert client.get("/api/v1/ai/health").status_code == 401


def test_stream_retry_is_idempotent_and_preserves_saved_message_ids(client, monkeypatch):
    create_account(client, "stream-idempotency@example.test")
    sign_in(client, "stream-idempotency@example.test")
    calls = []

    def stream_answer(*args, **kwargs):
        calls.append(1)
        return ({"provider": "ollama", "model": "qwen3:4b", "mode": "local",
                 "web_research_requested": False, "web_research_used": False,
                 "web_research_note": None, "sources": []}, iter(["Inflation is a sustained rise ", "in prices."]))

    monkeypatch.setattr(ai_engine, "stream_answer", stream_answer)
    created = post_csrf(client, "/api/v1/conversations", {"document_ids": [], "title": "Retry test", "mode": "local"})
    conversation_id = created.json()["id"]
    payload = {"question": "What is inflation?", "mode": "local", "request_id": "a" * 32}
    first = post_csrf(client, f"/api/v1/conversations/{conversation_id}/messages/stream", payload)
    second = post_csrf(client, f"/api/v1/conversations/{conversation_id}/messages/stream", payload)
    assert "event: done" in first.text and "event: done" in second.text
    assert len(calls) == 1
    assert first.text.count('"role": "user"') == 1
    history = client.get(f"/api/v1/conversations/{conversation_id}/messages").json()["messages"]
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert all(message["id"] for message in history)
    conflict = post_csrf(client, f"/api/v1/conversations/{conversation_id}/messages/stream", {
        **payload, "question": "How does credit work?",
    })
    assert conflict.status_code == 409


def test_stream_emits_sse_error_for_unexpected_database_exception_and_server_stays_healthy(client, monkeypatch):
    from backend.app.auth_store import AuthRepository

    create_account(client, "stream-error@example.test")
    sign_in(client, "stream-error@example.test")
    monkeypatch.setattr(ai_engine, "stream_answer", lambda *args, **kwargs: ({
        "provider": "ollama", "model": "qwen3:4b", "mode": "local",
        "web_research_requested": False, "web_research_used": False,
        "web_research_note": None, "sources": [],
    }, iter(["A partial answer."])))
    original_insert = AuthRepository.insert_one

    def fail_message_write(self, name, document):
        if name == "messages":
            raise OSError("synthetic storage failure")
        return original_insert(self, name, document)

    monkeypatch.setattr(AuthRepository, "insert_one", fail_message_write)
    conversation = post_csrf(client, "/api/v1/conversations", {"document_ids": [], "title": "Error test", "mode": "local"}).json()
    streamed = post_csrf(client, f"/api/v1/conversations/{conversation['id']}/messages/stream", {
        "question": "What is inflation?", "mode": "local",
    })
    assert streamed.status_code == 200
    assert "event: delta" in streamed.text
    assert "event: error" in streamed.text
    assert "server error" in streamed.text
    assert client.get("/health").status_code == 200


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path / "runtime")
    monkeypatch.setenv("APP_ENV", "test")
    with auth._LIMITER.lock:
        auth._LIMITER.events.clear()
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old_overrides)


def post_csrf(client, path, body, method="POST"):
    csrf = client.cookies.get("bankease_csrf")
    return client.request(method, path, json=body, headers={"X-CSRF-Token": csrf} if csrf else {})


def create_account(client, email, password="correct horse battery staple 2026"):
    client.get("/api/v1/auth/csrf")
    response = post_csrf(client, "/api/v1/auth/register", {
        "name": "Test User", "email": email, "password": password,
        "password_confirmation": password,
    })
    assert response.status_code == 202, response.text
    link = response.json()["development_verification_url"]
    token = parse_qs(urlparse(link).query)["verify"][0]
    verified = post_csrf(client, "/api/v1/auth/verify-email", {"token": token})
    assert verified.status_code == 200, verified.text


def sign_in(client, email, password="correct horse battery staple 2026"):
    response = post_csrf(client, "/api/v1/auth/login", {"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def test_registration_verification_login_logout_and_revocation(client):
    create_account(client, "person@example.test")
    user = sign_in(client, "person@example.test")
    assert user["email_verified"] is True
    assert client.get("/api/v1/auth/me").json()["user"]["email"] == "person@example.test"
    assert len(client.get("/api/v1/auth/sessions").json()["sessions"]) == 1

    assert post_csrf(client, "/api/v1/auth/logout", {}).status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_errors_do_not_reveal_if_email_exists(client):
    client.get("/api/v1/auth/csrf")
    unknown = post_csrf(client, "/api/v1/auth/login", {"email": "missing@example.test", "password": "wrong password"})
    create_account(client, "known@example.test")
    unverified = post_csrf(client, "/api/v1/auth/login", {"email": "known@example.test", "password": "wrong password"})
    assert unknown.status_code == unverified.status_code == 401
    assert unknown.json() == unverified.json() == {"detail": "Invalid email or password"}


def test_csrf_is_required_for_mutations(client):
    response = client.post("/api/v1/auth/register", json={
        "name": "Test", "email": "csrf@example.test", "password": "correct horse battery staple",
        "password_confirmation": "correct horse battery staple",
    })
    assert response.status_code == 403


def test_password_reset_revokes_old_session_and_allows_new_login(client):
    password = "correct horse battery staple 2026"
    create_account(client, "reset@example.test", password)
    sign_in(client, "reset@example.test", password)
    reset = post_csrf(client, "/api/v1/auth/forgot-password", {"email": "reset@example.test"})
    assert reset.status_code == 202
    token = parse_qs(urlparse(reset.json()["development_reset_url"]).query)["reset"][0]
    new_password = "another secure password 2026"
    changed = post_csrf(client, "/api/v1/auth/reset-password", {
        "token": token, "password": new_password, "password_confirmation": new_password,
    })
    assert changed.status_code == 200, changed.text
    assert client.get("/api/v1/auth/me").status_code == 401
    assert sign_in(client, "reset@example.test", new_password)["email"] == "reset@example.test"


def test_documents_are_private_to_the_account(client, tmp_path, monkeypatch):
    create_account(client, "first@example.test")
    sign_in(client, "first@example.test")
    pdf = tmp_path / "synthetic.pdf"
    make(pdf)
    with pdf.open("rb") as stream:
        uploaded = client.post("/documents/analyze", files={"file": (pdf.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    assert client.get(f"/documents/{document_id}/preview").status_code == 200
    archive = client.get("/api/v1/auth/export/archive")
    assert archive.status_code == 200
    assert archive.headers["cache-control"] == "private, no-store"
    with ZipFile(BytesIO(archive.content)) as bundle:
        assert f"documents/{document_id}.pdf" in bundle.namelist()
        exported_account = json.loads(bundle.read("account.json"))
        exported_doc = next(row for row in exported_account["documents"] if row["id"] == document_id)
        assert exported_doc["source_available"] is True
        assert "source_file" not in exported_doc
        assert bundle.read(f"documents/{document_id}.pdf").startswith(b"%PDF")
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["documents"][0]["archive_path"] == f"documents/{document_id}.pdf"
    monkeypatch.setattr(auth, "MAX_ACCOUNT_EXPORT_BYTES", 1)
    assert client.get("/api/v1/auth/export/archive").status_code == 413
    monkeypatch.setattr(auth, "MAX_ACCOUNT_EXPORT_BYTES", 100 * 1024 * 1024)

    assert post_csrf(client, "/api/v1/auth/logout", {}).status_code == 204
    create_account(client, "second@example.test")
    sign_in(client, "second@example.test")
    assert client.get(f"/documents/{document_id}").status_code == 404
    assert client.get(f"/documents/{document_id}/preview").status_code == 404
    assert client.get(f"/api/v1/documents/{document_id}/versions").status_code == 404
    assert client.get("/documents").json()["documents"] == []
    other_archive = client.get("/api/v1/auth/export/archive")
    assert other_archive.status_code == 200
    with ZipFile(BytesIO(other_archive.content)) as bundle:
        assert not any(name.startswith("documents/") for name in bundle.namelist())


def test_document_workspace_metadata_rename_preview_and_download(client, tmp_path):
    create_account(client, "document-workspace@example.test")
    sign_in(client, "document-workspace@example.test")
    pdf = tmp_path / "workspace-source.pdf"
    make(pdf)
    with pdf.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (pdf.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]

    listed = client.get("/api/v1/documents").json()["documents"]
    item = next(row for row in listed if row["id"] == document_id)
    assert item["source_available"] is True
    assert item["source_format"] == "pdf"

    renamed = post_csrf(client, f"/api/v1/documents/{document_id}", {"filename": "Quarterly loan review"}, method="PATCH")
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["filename"] == "Quarterly loan review.pdf"
    assert renamed.json()["source_available"] is True
    assert client.get("/api/v1/documents").json()["documents"][0]["filename"] == "Quarterly loan review.pdf"
    history = client.get(f"/api/v1/documents/{document_id}/versions").json()
    assert history["versions"][0]["filename"] == "Quarterly loan review.pdf"

    preview = client.get(f"/api/v1/documents/{document_id}/preview")
    download = client.get(f"/api/v1/documents/{document_id}/preview?download=true")
    assert preview.status_code == 200 and preview.headers["content-disposition"] == "inline"
    assert download.status_code == 200 and download.headers["content-disposition"].startswith("attachment;")
    assert "Quarterly%20loan%20review.pdf" in download.headers["content-disposition"]
    assert download.content.startswith(b"%PDF")

    wrong_extension = post_csrf(client, f"/api/v1/documents/{document_id}", {"filename": "other.txt"}, method="PATCH")
    assert wrong_extension.status_code == 422
    unsafe_path = post_csrf(client, f"/api/v1/documents/{document_id}", {"filename": "../unsafe.pdf"}, method="PATCH")
    assert unsafe_path.status_code == 422
    too_long = post_csrf(client, f"/api/v1/documents/{document_id}", {"filename": "x" * 121}, method="PATCH")
    assert too_long.status_code == 422
    assert client.get(f"/api/v1/documents/{document_id}").json()["filename"] == "Quarterly loan review.pdf"


def test_account_deletion_removes_original_source_files(client, tmp_path):
    create_account(client, "cleanup@example.test")
    sign_in(client, "cleanup@example.test")
    pdf = tmp_path / "synthetic.pdf"
    make(pdf)
    with pdf.open("rb") as stream:
        uploaded = client.post("/documents/analyze", files={"file": (pdf.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    source = store.ROOT / "uploads" / f"{document_id}.pdf"
    assert source.is_file()
    response = client.request("DELETE", "/api/v1/auth/me", json={
        "password": "correct horse battery staple 2026", "confirmation": "DELETE",
    }, headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert response.status_code == 204, response.text
    assert not source.exists()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_review_request_assignment_is_scoped_to_owner_and_assignee(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BANKEASE_ADMIN_EMAILS", "admin@example.test")
    create_account(client, "admin@example.test")
    create_account(client, "reviewer@example.test")
    create_account(client, "owner@example.test")

    admin = sign_in(client, "admin@example.test")
    assert admin["role"] == "system_admin"
    csrf = client.cookies.get("bankease_csrf")
    role = client.patch("/api/v1/admin/users/role", json={"email": "reviewer@example.test", "role": "document_reviewer"},
                        headers={"X-CSRF-Token": csrf})
    assert role.status_code == 200, role.text
    assert role.json()["user"]["role"] == "document_reviewer"
    post_csrf(client, "/api/v1/auth/logout", {})

    client.get("/api/v1/auth/csrf")
    sign_in(client, "owner@example.test")
    pdf = tmp_path / "review-loan.pdf"
    make(pdf)
    with pdf.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (pdf.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    request_body = {"document_id": document_id, "field_id": "loan_amount", "reason": "Please check the number on the sanction page."}
    requested = post_csrf(client, "/api/v1/reviews", request_body)
    assert requested.status_code == 201, requested.text
    task_id = requested.json()["id"]
    duplicate = post_csrf(client, "/api/v1/reviews", request_body)
    assert duplicate.status_code == 201 and duplicate.json()["created"] is False
    assert client.get("/api/v1/reviews").json()["reviews"][0]["status"] == "submitted"
    post_csrf(client, "/api/v1/auth/logout", {})

    client.get("/api/v1/auth/csrf")
    sign_in(client, "admin@example.test")
    assigned = post_csrf(client, f"/api/v1/reviews/{task_id}/assign", {"reviewer_email": "reviewer@example.test"})
    assert assigned.status_code == 200, assigned.text
    post_csrf(client, "/api/v1/auth/logout", {})

    client.get("/api/v1/auth/csrf")
    sign_in(client, "reviewer@example.test")
    queue = client.get("/api/v1/reviews").json()["reviews"]
    assert len(queue) == 1 and queue[0]["snapshot"]["evidence"]
    assert client.get(f"/api/v1/documents/{document_id}").status_code == 404
    decision = post_csrf(client, f"/api/v1/reviews/{task_id}/decision", {"decision": "needs_owner_action", "comment": "Confirm this against your signed copy."})
    assert decision.status_code == 200, decision.text
    post_csrf(client, "/api/v1/auth/logout", {})

    client.get("/api/v1/auth/csrf")
    sign_in(client, "owner@example.test")
    owner_task = client.get(f"/api/v1/reviews?document_id={document_id}").json()["reviews"][0]
    assert owner_task["status"] == "needs_owner_action"
    response = post_csrf(client, f"/api/v1/reviews/{task_id}/response", {"message": "I checked the signed page."})
    assert response.status_code == 200
    assert client.get("/api/v1/reviews").json()["reviews"][0]["status"] == "owner_responded"
    removed = client.delete(f"/api/v1/documents/{document_id}", headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert removed.status_code == 204
    assert client.get("/api/v1/reviews").json()["reviews"] == []


def test_statement_transaction_review_is_assigned_evidence_scoped_and_audited(client, monkeypatch):
    monkeypatch.setenv("BANKEASE_ADMIN_EMAILS", "admin@example.test")
    create_account(client, "admin@example.test")
    create_account(client, "reviewer@example.test")
    create_account(client, "statement-owner@example.test")

    sign_in(client, "admin@example.test")
    role = client.patch("/api/v1/admin/users/role", json={"email": "reviewer@example.test", "role": "document_reviewer"},
                        headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert role.status_code == 200, role.text
    post_csrf(client, "/api/v1/auth/logout", {})

    client.get("/api/v1/auth/csrf")
    sign_in(client, "statement-owner@example.test")
    statement = ROOT / "data" / "samples" / "statements" / "review_flags.csv"
    with statement.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (statement.name, stream, "text/csv")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]

    missing = post_csrf(client, "/api/v1/reviews", {"document_id": document_id, "field_id": "transaction:99"})
    assert missing.status_code == 404
    created = post_csrf(client, "/api/v1/reviews", {
        "document_id": document_id, "field_id": "transaction:3", "reason": "Please check this possible duplicate."
    })
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    duplicate = post_csrf(client, "/api/v1/reviews", {"document_id": document_id, "field_id": "transaction:3"})
    assert duplicate.status_code == 201 and duplicate.json()["created"] is False
    post_csrf(client, "/api/v1/auth/logout", {})

    client.get("/api/v1/auth/csrf")
    sign_in(client, "admin@example.test")
    assigned = post_csrf(client, f"/api/v1/reviews/{task_id}/assign", {"reviewer_email": "reviewer@example.test"})
    assert assigned.status_code == 200, assigned.text
    post_csrf(client, "/api/v1/auth/logout", {})

    client.get("/api/v1/auth/csrf")
    sign_in(client, "reviewer@example.test")
    queue = client.get("/api/v1/reviews").json()["reviews"]
    assert len(queue) == 1
    snapshot = queue[0]["snapshot"]
    assert snapshot["kind"] == "statement_transaction"
    assert snapshot["row_number"] == 3
    assert snapshot["source_values"]["source_values"]["narration"] == "Groceries"
    assert client.get(f"/api/v1/documents/{document_id}").status_code == 404
    decision = post_csrf(client, f"/api/v1/reviews/{task_id}/decision", {
        "decision": "needs_owner_action", "comment": "Please confirm the merchant and receipt."
    })
    assert decision.status_code == 200, decision.text
    post_csrf(client, "/api/v1/auth/logout", {})

    client.get("/api/v1/auth/csrf")
    sign_in(client, "statement-owner@example.test")
    task = client.get(f"/api/v1/reviews?document_id={document_id}").json()["reviews"][0]
    assert task["status"] == "needs_owner_action"
    response = post_csrf(client, f"/api/v1/reviews/{task_id}/response", {"message": "I checked the receipt."})
    assert response.status_code == 200
    task = client.get(f"/api/v1/reviews?document_id={document_id}").json()["reviews"][0]
    assert [event["action"] for event in task["history"]][-2:] == ["needs_owner_action", "owner_responded"]


def test_customer_cannot_open_admin_user_list(client):
    create_account(client, "customer@example.test")
    sign_in(client, "customer@example.test")
    assert client.get("/api/v1/admin/users").status_code == 403


def test_global_search_returns_matches_only_from_the_signed_in_owner(client, tmp_path):
    create_account(client, "search-owner@example.test")
    create_account(client, "other-owner@example.test")
    sign_in(client, "search-owner@example.test")
    pdf = tmp_path / "search-loan.pdf"
    make(pdf)
    with pdf.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (pdf.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    matches = client.get("/api/v1/search?q=borrower%20shall")
    assert matches.status_code == 200
    assert matches.json()["results"]
    assert all(result["document_id"] == document_id for result in matches.json()["results"])
    post_csrf(client, "/api/v1/auth/logout", {})
    client.get("/api/v1/auth/csrf")
    sign_in(client, "other-owner@example.test")
    assert client.get("/api/v1/search?q=borrower%20shall").json()["results"] == []


def test_dashboard_summary_uses_only_saved_owner_documents_and_findings(client, tmp_path):
    create_account(client, "dashboard-owner@example.test")
    create_account(client, "dashboard-other@example.test")
    sign_in(client, "dashboard-owner@example.test")
    pdf = tmp_path / "dashboard-loan.pdf"
    make(pdf)
    with pdf.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (pdf.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]

    summary = client.get("/api/v1/dashboard/summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["documents_analyzed"] == 1
    assert body["documents"][0]["id"] == document_id
    assert body["finding_count"] == sum(body["findings_by_category"].values())
    assert body["upcoming_obligations"] == []
    post_csrf(client, "/api/v1/auth/logout", {})
    client.get("/api/v1/auth/csrf")
    sign_in(client, "dashboard-other@example.test")
    other = client.get("/api/v1/dashboard/summary").json()
    assert other["documents"] == []
    assert other["documents_analyzed"] == other["finding_count"] == other["review_count"] == 0


def test_background_upload_job_is_owner_scoped_retryable_and_completes(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BANKEASE_ASYNC_DOCUMENTS", "true")
    create_account(client, "job-owner@example.test")
    create_account(client, "job-other@example.test")
    sign_in(client, "job-owner@example.test")
    pdf = tmp_path / "queued-loan.pdf"
    make(pdf)
    with pdf.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (pdf.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 202, uploaded.text
    document_id = uploaded.json()["id"]
    assert uploaded.json()["status"] == "processing"
    status = client.get(f"/api/v1/documents/{document_id}/status").json()
    assert status["job"]["status"] == "queued"

    repository = AuthRepository(root=store.ROOT)
    assert jobs.process_next(repository, worker_id="test-worker") is True
    completed = client.get(f"/api/v1/documents/{document_id}/status").json()
    assert completed["status"] == completed["job"]["status"] == "completed"
    assert client.get(f"/api/v1/documents/{document_id}/fields").json()["loan_amount"]["value"] == 500000

    failed_upload = client.post("/api/v1/documents", files={"file": ("unsupported.csv", b"not,a,statement\n", "text/csv")},
                                headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert failed_upload.status_code == 202
    failed_id = failed_upload.json()["id"]
    assert jobs.process_next(repository, worker_id="test-worker") is True
    failed = client.get(f"/api/v1/documents/{failed_id}/status").json()
    assert failed["status"] == failed["job"]["status"] == "failed"
    assert failed["error"]
    retried = post_csrf(client, f"/api/v1/documents/{failed_id}/retry", {})
    assert retried.status_code == 202 and retried.json()["status"] == "processing"
    assert jobs.process_next(repository, worker_id="test-worker") is True
    assert client.get(f"/api/v1/documents/{failed_id}/status").json()["status"] == "failed"

    post_csrf(client, "/api/v1/auth/logout", {})
    client.get("/api/v1/auth/csrf")
    sign_in(client, "job-other@example.test")
    assert client.get(f"/api/v1/documents/{document_id}/status").status_code == 404
    assert client.post(f"/api/v1/documents/{document_id}/retry", json={},
                       headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")}).status_code == 404
    post_csrf(client, "/api/v1/auth/logout", {})
    client.get("/api/v1/auth/csrf")
    sign_in(client, "job-owner@example.test")
    assert client.delete(f"/api/v1/documents/{document_id}",
                         headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")}).status_code == 204
    assert repository.find_one("processing_jobs", {"_id": document_id}) is None
    assert client.delete(f"/api/v1/documents/{failed_id}",
                         headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")}).status_code == 204
    assert repository.find_one("processing_jobs", {"_id": failed_id}) is None


def test_document_conversations_persist_with_evidence_and_enforce_owner_scope(client, tmp_path):
    create_account(client, "chat-owner@example.test")
    create_account(client, "chat-other@example.test")
    sign_in(client, "chat-owner@example.test")
    pdf = tmp_path / "chat-loan.pdf"
    make(pdf)
    with pdf.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (pdf.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]

    created = post_csrf(client, "/api/v1/conversations", {"document_id": document_id, "title": "Loan questions"})
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    sent = post_csrf(client, f"/api/v1/conversations/{conversation_id}/messages", {
        "question": "What is the loan amount?",
    })
    assert sent.status_code == 200, sent.text
    assert sent.json()["answer"]["sources"]
    history = client.get(f"/api/v1/conversations/{conversation_id}/messages")
    assert [message["role"] for message in history.json()["messages"]] == ["user", "assistant"]
    assert history.json()["messages"][1]["sources"] == sent.json()["answer"]["sources"]
    exported = client.get("/api/v1/auth/export").json()
    assert exported["conversations"][0]["id"] == conversation_id
    assert len(exported["conversations"][0]["messages"]) == 2

    post_csrf(client, "/api/v1/auth/logout", {})
    client.get("/api/v1/auth/csrf")
    sign_in(client, "chat-other@example.test")
    assert client.get(f"/api/v1/conversations/{conversation_id}/messages").status_code == 404
    assert post_csrf(client, f"/api/v1/conversations/{conversation_id}/messages", {
        "question": "What is the loan amount?",
    }).status_code == 404
    assert client.delete(f"/api/v1/conversations/{conversation_id}",
                         headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")}).status_code == 404

    post_csrf(client, "/api/v1/auth/logout", {})
    client.get("/api/v1/auth/csrf")
    sign_in(client, "chat-owner@example.test")
    removed = client.delete(f"/api/v1/conversations/{conversation_id}",
                            headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert removed.status_code == 204
    assert client.get(f"/api/v1/conversations/{conversation_id}/messages").status_code == 404
    conversation_id = post_csrf(client, "/api/v1/conversations", {
        "document_id": document_id, "title": "Second conversation",
    }).json()["id"]
    assert client.delete(f"/api/v1/documents/{document_id}",
                         headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")}).status_code == 204
    assert client.get(f"/api/v1/conversations/{conversation_id}/messages").status_code == 404


def test_multi_document_conversation_cites_each_source_and_cascades_on_document_delete(client, tmp_path):
    create_account(client, "multi-chat-owner@example.test")
    create_account(client, "multi-chat-other@example.test")
    sign_in(client, "multi-chat-owner@example.test")
    document_ids = []
    filenames = ["first-loan.pdf", "second-loan.pdf"]
    for filename in filenames:
        pdf = tmp_path / filename
        make(pdf)
        with pdf.open("rb") as stream:
            uploaded = client.post("/api/v1/documents", files={"file": (filename, stream, "application/pdf")},
                                   headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
        assert uploaded.status_code == 201, uploaded.text
        document_ids.append(uploaded.json()["id"])

    created = post_csrf(client, "/api/v1/conversations", {
        "document_ids": document_ids, "title": "Compare loan terms",
    })
    assert created.status_code == 201, created.text
    conversation = created.json()
    assert conversation["document_ids"] == document_ids
    assert conversation["document_filenames"] == filenames

    sent = post_csrf(client, f"/api/v1/conversations/{conversation['id']}/messages", {
        "question": "What interest rate is stated?",
    })
    assert sent.status_code == 200, sent.text
    sources = sent.json()["answer"]["sources"]
    assert {source["document_id"] for source in sources} == set(document_ids)
    assert {source["filename"] for source in sources} == set(filenames)
    assert all(source["page"] and source["text"] for source in sources)
    history = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()
    assert history["conversation"]["document_ids"] == document_ids
    assert history["messages"][-1]["sources"] == sources
    exported = client.get("/api/v1/auth/export").json()["conversations"][0]
    assert exported["document_ids"] == document_ids
    assert exported["messages"][-1]["sources"] == sources

    unsupported = post_csrf(client, f"/api/v1/conversations/{conversation['id']}/messages", {
        "question": "What color is the borrower's car?",
    })
    assert unsupported.status_code == 200
    assert unsupported.json()["answer"]["found"] is False
    assert unsupported.json()["answer"]["sources"] == []

    post_csrf(client, "/api/v1/auth/logout", {})
    client.get("/api/v1/auth/csrf")
    sign_in(client, "multi-chat-other@example.test")
    denied = post_csrf(client, "/api/v1/conversations", {
        "document_ids": document_ids, "title": "Cross-account attempt",
    })
    assert denied.status_code == 404

    post_csrf(client, "/api/v1/auth/logout", {})
    client.get("/api/v1/auth/csrf")
    sign_in(client, "multi-chat-owner@example.test")
    deleted = client.delete(f"/api/v1/documents/{document_ids[1]}",
                            headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/conversations/{conversation['id']}/messages").status_code == 404


def test_insurance_policy_fields_are_evidence_backed_correctable_and_answerable(client, tmp_path):
    import pymupdf

    create_account(client, "policy-owner@example.test")
    sign_in(client, "policy-owner@example.test")
    policy = pymupdf.open()
    policy.new_page().insert_text((72, 72), "\n".join([
        "Health Insurance Policy Schedule",
        "Insurance Company: Example Assurance Ltd",
        "Product Name: Family Health Protect",
        "Policy Number: HLT-2026-9031",
        "Policyholder: Asha Rao",
        "Sum Insured: INR 500,000",
        "Annual Premium: INR 18,500",
        "Policy Start Date: 1 April 2026",
        "Policy Expiry Date: 31 March 2027",
        "EXCLUSIONS",
        "Pre-existing conditions are excluded during the stated waiting period.",
        "WAITING PERIOD",
        "A waiting period applies to specified treatments.",
        "CLAIMS",
        "The policyholder must notify the insurer and submit the listed documents.",
    ]))
    path = tmp_path / "family-health-policy.pdf"
    policy.save(path)
    policy.close()
    with path.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (path.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    assert document["doc_type"] == "insurance_policy"
    assert document["status"] == "completed"
    assert "does not verify active coverage" in document["analysis_note"]
    fields = client.get(f"/api/v1/documents/{document['id']}/fields").json()
    assert fields["policy_number"]["value"] == "HLT-2026-9031"
    assert fields["insurer"]["value"] == "Example Assurance Ltd"
    assert fields["product_name"]["value"] == "Family Health Protect"
    assert fields["policyholder"]["value"] == "Asha Rao"
    assert fields["sum_insured"]["value"] == 500000
    assert fields["premium_amount"]["value"] == 18500
    assert fields["policy_expiry_date"]["value"] == "2027-03-31"
    assert fields["policy_expiry_date"]["page"] == 1
    assert fields["policy_expiry_date"]["evidence"] == "Policy Expiry Date: 31 March 2027"
    assert any(clause["category"] == "Exclusions" and clause["page"] == 1
               for clause in client.get(f"/api/v1/documents/{document['id']}/clauses").json())

    conversation = post_csrf(client, "/api/v1/conversations", {
        "document_id": document["id"], "title": "Policy questions",
    })
    assert conversation.status_code == 201, conversation.text
    answer = post_csrf(client, f"/api/v1/conversations/{conversation.json()['id']}/messages", {
        "question": "Which exclusions are stated?",
    })
    assert answer.status_code == 200, answer.text
    assert answer.json()["answer"]["found"] is True
    assert answer.json()["answer"]["sources"][0]["filename"] == path.name
    assert answer.json()["answer"]["sources"][0]["page"] == 1

    csrf_headers = {"X-CSRF-Token": client.cookies.get("bankease_csrf")}
    corrected = client.patch(f"/api/v1/documents/{document['id']}/fields/insurer", json={
        "value": "Example Cover Ltd", "reason": "Confirmed from the policy schedule.",
    }, headers=csrf_headers)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["value"] == "Example Assurance Ltd"
    assert corrected.json()["effective_value"] == "Example Cover Ltd"
    assert corrected.json()["evidence"] == "Insurance Company: Example Assurance Ltd"
    expiry = client.patch(f"/api/v1/documents/{document['id']}/fields/policy_expiry_date", json={
        "value": "2027-03-31", "reason": "Confirmed against the policy schedule.",
    }, headers=csrf_headers)
    assert expiry.status_code == 200, expiry.text
    obligations = client.get("/api/v1/dashboard/summary").json()["upcoming_obligations"]
    assert len(obligations) == 1
    assert obligations[0]["title"] == "Insurance policy renewal"
    assert obligations[0]["document_id"] == document["id"]
    assert obligations[0]["confirmed_by_owner"] is True
    assert obligations[0]["date_evidence"] == "Policy Expiry Date: 31 March 2027"
    assert post_csrf(client, f"/api/v1/documents/{document['id']}/calculate-emi", {}).status_code == 409


def test_insurance_policy_classification_requires_policy_evidence():
    from backend.app import insurance

    assert insurance.is_policy_document(["Insurance policy. Policy Number: ABC-1234\nSum Insured: INR 200,000"])
    assert not insurance.is_policy_document(["Loan Agreement\nInsurance policy premium is financed with the loan."])
    assert not insurance.is_policy_document(["Insurance claims hotline and household emergency contacts."])


def test_kyc_form_extraction_is_labeled_limited_and_owner_scoped(client, tmp_path):
    import pymupdf
    from backend.app import extract, kyc

    create_account(client, "kyc-owner@example.test")
    sign_in(client, "kyc-owner@example.test")
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "\n".join([
        "Customer Identification Form - KYC",
        "Full Name: Meera Shah",
        "Date of Birth: 5 March 1990",
        "Residential Address: 41 Sample Road, Pune",
        "PAN Number: ABCDE1234F",
        "Aadhaar Number: 1111 2222 3333",
    ]))
    path = tmp_path / "customer-kyc-form.pdf"
    pdf.save(path)
    pdf.close()
    with path.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (path.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    assert document["doc_type"] == "kyc_form"
    assert document["status"] == "completed"
    assert "do not verify a person's identity" in document["analysis_note"]
    fields = client.get(f"/api/v1/documents/{document['id']}/fields").json()
    assert fields["full_name"]["value"] == "Meera Shah"
    assert fields["full_name"]["page"] == 1
    assert fields["date_of_birth"]["value"] == "1990-03-05"
    assert fields["date_of_birth"]["evidence"] == "Date of Birth: 5 March 1990"
    assert fields["address"]["value"] == "41 Sample Road, Pune"
    assert not {"pan_number", "aadhaar_number", "identity_number"} & set(fields)
    assert client.get("/api/v1/search", params={"q": "ABCDE1234F"}).json()["results"] == []
    assert client.get("/api/v1/search", params={"q": "Meera Shah"}).json()["results"] == []
    assert client.get(f"/api/v1/documents/{document['id']}/clauses").json() == []
    assert client.post(f"/api/v1/documents/{document['id']}/ask", json={
        "question": "What is the name on this form?",
    }, headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")}).status_code == 409

    corrected = client.patch(f"/api/v1/documents/{document['id']}/fields/full_name", json={
        "value": "Meera S. Shah", "reason": "Checked the original form.",
    }, headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["effective_value"] == "Meera S. Shah"
    assert client.get("/api/v1/dashboard/summary").json()["upcoming_obligations"] == []

    post_csrf(client, "/api/v1/auth/logout", {})
    client.get("/api/v1/auth/csrf")
    create_account(client, "kyc-other@example.test")
    sign_in(client, "kyc-other@example.test")
    assert client.get(f"/api/v1/documents/{document['id']}").status_code == 404

    assert extract.classify(["Customer Identification Form - KYC\nFull Name: A\nDate of Birth: 1 Jan 2000"])[0] == "kyc_form"
    assert extract.classify(["Loan Agreement\nKYC details include Full Name: A\nDate of Birth: 1 Jan 2000"])[0] != "kyc_form"
    assert extract.classify(["Aadhaar Card\nFull Name: A\nDate of Birth: 1 Jan 2000"])[0] != "kyc_form"
    conflict = kyc.extract_kyc_fields([
        "KYC Form\nFull Name: Meera Shah\nDate of Birth: 5 March 1990\nResidential Address: 41 Sample Road",
        "Date of Birth: 6 March 1990",
    ])
    assert conflict["date_of_birth"]["value"] is None
    assert conflict["date_of_birth"]["status"] == "review"


def test_financial_statement_totals_have_source_checks_and_recalculate_after_correction(client, tmp_path):
    import pymupdf

    create_account(client, "financial-statement-owner@example.test")
    sign_in(client, "financial-statement-owner@example.test")
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "\n".join([
        "Balance Sheet",
        "Total Assets: INR 900,000",
        "Total Liabilities: INR 600,000",
        "Total Equity: INR 350,000",
        "Total Revenue: INR 2,000,000",
        "Total Expenses: INR 1,700,000",
        "Profit After Tax: INR 300,000",
    ]))
    path = tmp_path / "sample-balance-sheet.pdf"
    pdf.save(path)
    pdf.close()
    with path.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (path.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    assert document["doc_type"] == "financial_statement"
    assert document["status"] == "needs_review"
    fields = client.get(f"/api/v1/documents/{document['id']}/fields").json()
    assert fields["total_assets"]["value"] == "900000"
    assert fields["total_assets"]["evidence"] == "Total Assets: INR 900,000"
    opened = client.get(f"/api/v1/documents/{document['id']}").json()
    check = opened["financial_checks"][0]
    assert check["status"] == "difference"
    assert check["difference"] == "-50000"
    assert len(check["sources"]) == 3 and all(source["page"] == 1 for source in check["sources"])
    conversation = post_csrf(client, "/api/v1/conversations", {
        "document_id": document["id"], "title": "Balance sheet review",
    })
    assert conversation.status_code == 201, conversation.text
    answer = post_csrf(client, f"/api/v1/conversations/{conversation.json()['id']}/messages", {
        "question": "What total assets are shown?",
    })
    assert answer.status_code == 200 and answer.json()["answer"]["sources"][0]["page"] == 1
    summary = client.get("/api/v1/dashboard/summary").json()
    assert summary["findings_by_category"]["Financial statement arithmetic"] == 1
    assert summary["financial_signals"] == 1 and summary["review_count"] >= 1
    assert client.get("/api/v1/search", params={"q": "labeled totals differ"}).json()["results"][0]["kind"] == "finding"

    corrected = client.patch(f"/api/v1/documents/{document['id']}/fields/total_equity", json={
        "value": "300,000", "reason": "Checked the reported value on page 1.",
    }, headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert corrected.status_code == 200, corrected.text
    check = client.get(f"/api/v1/documents/{document['id']}").json()["financial_checks"][0]
    assert check["status"] == "consistent" and check["difference"] == "0"
    corrected_summary = client.get("/api/v1/dashboard/summary").json()
    assert corrected_summary["findings_by_category"].get("Financial statement arithmetic") is None
    assert corrected_summary["financial_signals"] == 0
    reverted = client.patch(f"/api/v1/documents/{document['id']}/fields/total_equity", json={
        "value": None, "reason": "Revert and restore imported value.",
    }, headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert reverted.status_code == 200
    assert client.get(f"/api/v1/documents/{document['id']}").json()["financial_checks"][0]["status"] == "difference"
    assert post_csrf(client, f"/api/v1/documents/{document['id']}/calculate-emi", {}).status_code == 409
    from backend.app import financial_statements
    comparative = financial_statements.extract_fields([
        "Balance Sheet\nTotal Assets: 900,000 850,000\nTotal Liabilities: 600,000\nTotal Equity: 300,000",
    ])
    assert comparative["total_assets"]["status"] == "review"
    assert financial_statements.verify_totals(comparative)[0]["status"] == "not_evaluated"
    malformed_group = financial_statements.extract_fields([
        "Balance Sheet\nTotal Assets: 1,2345,678\nTotal Liabilities: 600\nTotal Equity: 300",
    ])
    assert malformed_group["total_assets"]["status"] == "review"


def test_financial_statement_comparison_requires_matching_cited_units_and_is_owner_scoped(client, tmp_path):
    import pymupdf

    create_account(client, "financial-comparison-owner@example.test")
    sign_in(client, "financial-comparison-owner@example.test")

    def upload_report(filename, assets, period):
        pdf = pymupdf.open()
        pdf.new_page().insert_text((72, 72), "\n".join([
            "Balance Sheet",
            f"For the year ended {period}",
            f"Total Assets (INR in lakhs): {assets}",
            "Total Liabilities (INR in lakhs): 600",
            "Total Equity (INR in lakhs): 300",
        ]))
        path = tmp_path / filename
        pdf.save(path)
        pdf.close()
        with path.open("rb") as stream:
            uploaded = client.post("/api/v1/documents", files={"file": (path.name, stream, "application/pdf")},
                                   headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
        assert uploaded.status_code == 201, uploaded.text
        return uploaded.json()

    report_a = upload_report("financial-year-a.pdf", "900", "31 March 2025")
    report_b = upload_report("financial-year-b.pdf", "950", "31 March 2026")
    compared = post_csrf(client, "/api/v1/comparisons/financial-statements", {
        "document_id_a": report_a["id"], "document_id_b": report_b["id"],
    })
    assert compared.status_code == 200, compared.text
    result = compared.json()
    assets = next(row for row in result["rows"] if row["field"] == "total_assets")
    assert assets["status"] == "potential_difference"
    assert assets["difference_b_minus_a"] == "50"
    assert assets["unit"] == {"currency": "inr", "scale": "lakh"}
    assert assets["a"]["page"] == assets["b"]["page"] == 1
    assert result["a"]["reporting_period"]["date"] == "2025-03-31"
    assert result["b"]["reporting_period"]["date"] == "2026-03-31"
    assert result["a"]["reporting_period"]["status"] == "explicit"

    history = client.get("/api/v1/comparisons?kind=financial_statement")
    assert history.status_code == 200
    assert history.json()["comparisons"][0]["kind"] == "financial_statement"
    snapshot = client.get(f"/api/v1/comparisons/{result['comparison_id']}")
    assert snapshot.status_code == 200 and snapshot.json()["result"]["rows"] == result["rows"]
    pdf_report = client.get(f"/api/v1/comparisons/{result['comparison_id']}/report")
    assert pdf_report.status_code == 200 and pdf_report.headers["content-type"] == "application/pdf"

    from backend.app import financial_statements
    lakh_terms = financial_statements.extract_fields(["Balance Sheet\nTotal Assets (INR in lakhs): 900"])
    crore_terms = financial_statements.extract_fields(["Balance Sheet\nTotal Assets (INR in crores): 9"])
    unit_mismatch = financial_statements.compare_statements(
        {"id": "a", "filename": "a.pdf", "pages": []}, lakh_terms,
        {"id": "b", "filename": "b.pdf", "pages": []}, crore_terms,
    )
    assert unit_mismatch["rows"][0]["status"] == "not_comparable"
    assert unit_mismatch["rows"][0]["difference_b_minus_a"] is None
    ambiguous_period = financial_statements.extract_reporting_period([
        "Balance Sheet\nAs at 31 March 2025\nAs at 31 March 2024",
    ])
    assert ambiguous_period["status"] == "ambiguous"

    post_csrf(client, "/api/v1/auth/logout", {})
    client.get("/api/v1/auth/csrf")
    create_account(client, "financial-comparison-other@example.test")
    sign_in(client, "financial-comparison-other@example.test")
    forbidden = post_csrf(client, "/api/v1/comparisons/financial-statements", {
        "document_id_a": report_a["id"], "document_id_b": report_b["id"],
    })
    assert forbidden.status_code == 404


def test_audit_report_passages_are_quoted_cited_and_never_interpreted_as_misconduct(client, tmp_path):
    import pymupdf
    from backend.app import audit_reports, extract

    create_account(client, "audit-report-owner@example.test")
    sign_in(client, "audit-report-owner@example.test")
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "\n".join([
        "Independent Auditor's Report",
        "Audit Firm: Rao and Company",
        "Reporting Period: Year ended 31 March 2026",
        "Report Date: 15 May 2026",
        "Opinion: Qualified opinion",
        "Opinion",
        "We have audited the accompanying financial statements of Example Limited.",
        "In our opinion, except for the matter described in the Basis for Qualified Opinion, the statements present fairly.",
        "",
        "Basis for Qualified Opinion",
        "The inventory observation was limited for the reasons described in note 12.",
        "The company is assessing the impact and will provide a management response.",
        "",
        "Key Audit Matters",
        "Valuation of inventory required significant auditor attention.",
    ]))
    path = tmp_path / "independent-audit-report.pdf"
    pdf.save(path)
    pdf.close()
    with path.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (path.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    assert document["doc_type"] == "audit_report" and document["status"] == "needs_review"
    assert "not independently interpreted" in document["analysis_note"]
    fields = client.get(f"/api/v1/documents/{document['id']}/fields").json()
    assert fields["auditor_name"]["value"] == "Rao and Company"
    assert fields["reporting_period"]["page"] == 1
    assert fields["audit_opinion"]["value"] == "Qualified opinion"
    passages = client.get(f"/api/v1/documents/{document['id']}/clauses").json()
    assert {item["category"] for item in passages} >= {"Auditor opinion", "Basis for opinion", "Key audit matters"}
    assert all(item["page"] == 1 and item["evidence"] == item["text"] for item in passages)
    assert all("misconduct" in item["attention_point"].lower() or "complete report" in item["attention_point"].lower() for item in passages)
    summary = client.get("/api/v1/dashboard/summary").json()
    assert summary["audit_signals"] == 1 and summary["review_count"] >= 1
    assert summary["finding_count"] == 0
    assert extract.classify(["Loan Agreement\nOpinion: This report was audited."])[0] != "audit_report"
    assert not audit_reports.is_audit_report(["Auditor's letter\nOpinion\nSome text."])

    conversation = post_csrf(client, "/api/v1/conversations", {
        "document_id": document["id"], "title": "Audit report review",
    })
    assert conversation.status_code == 201, conversation.text
    answer = post_csrf(client, f"/api/v1/conversations/{conversation.json()['id']}/messages", {
        "question": "Qualified opinion",
    })
    assert answer.status_code == 200 and answer.json()["answer"]["sources"][0]["page"] == 1
    report = client.get(f"/api/v1/documents/{document['id']}/report")
    assert report.status_code == 200 and "Basis for opinion" in report.text


def test_scanned_insurance_policy_requires_review_before_owner_confirms_renewal(client, tmp_path, monkeypatch):
    import pymupdf
    from backend.app import main as main_module, ocr

    create_account(client, "scanned-policy-owner@example.test")
    sign_in(client, "scanned-policy-owner@example.test")
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_image(page.rect, stream=b"P6\n2 2\n255\n" + bytes([255, 255, 255] * 4))
    path = tmp_path / "scanned-insurance-policy.pdf"
    pdf.save(path)
    pdf.close()
    recognized = "\n".join([
        "Health Insurance Policy Schedule",
        "Policy Number: HLT-2026-1234",
        "Sum Insured: INR 250,000",
        "Policy Expiry Date: 31 March 2027",
        "Exclusions apply as described in the schedule.",
    ])
    monkeypatch.setattr(main_module.ocr, "extract_pages", lambda _pdf, indexes: {
        0: ocr.OCRPage(recognized, [{"text": "Policy Expiry Date: 31 March 2027", "bbox": [24, 80, 250, 96]}]),
    })
    with path.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (path.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    assert document["doc_type"] == "insurance_policy"
    assert document["status"] == "needs_review"
    expiry = client.get(f"/api/v1/documents/{document['id']}/fields").json()["policy_expiry_date"]
    assert expiry["value"] == "2027-03-31" and expiry["extraction_method"] == "pymupdf_tesseract_ocr"
    assert client.get("/api/v1/dashboard/summary").json()["upcoming_obligations"] == []
    confirmed = client.patch(f"/api/v1/documents/{document['id']}/fields/policy_expiry_date", json={
        "value": "2027-03-31", "reason": "Checked the scanned policy schedule.",
    }, headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert confirmed.status_code == 200, confirmed.text
    obligations = client.get("/api/v1/dashboard/summary").json()["upcoming_obligations"]
    assert len(obligations) == 1 and obligations[0]["title"] == "Insurance policy renewal"


def test_insurance_ambiguous_expiry_and_conflicting_values_stay_for_review():
    from backend.app import insurance

    ambiguous = insurance.extract_policy_terms(["Insurance Policy Schedule\nPolicy Expiry Date: 05/06/2027"])
    assert ambiguous["policy_expiry_date"]["value"] is None
    assert ambiguous["policy_expiry_date"]["status"] == "review"
    assert "ambiguous" in ambiguous["policy_expiry_date"]["note"]
    conflict = insurance.extract_policy_terms([
        "Insurance Policy Schedule\nSum Insured: INR 200,000\nSum Insured: INR 300,000"
    ])
    assert conflict["sum_insured"]["value"] is None
    assert conflict["sum_insured"]["status"] == "review"
    assert conflict["sum_insured"]["page"] == 1
    explicit_currency = insurance.extract_policy_terms([
        "Insurance Policy Schedule\nCurrency: INR\nSum Insured: 200,000"
    ])
    assert explicit_currency["sum_insured"]["value"] == 200000
    assert explicit_currency["sum_insured"]["currency"] == "INR"
    unknown_currency = insurance.extract_policy_terms([
        "Insurance Policy Schedule\nSum Insured: 200,000"
    ])
    assert unknown_currency["sum_insured"]["value"] is None
    assert unknown_currency["sum_insured"]["status"] == "review"


def test_account_deletion_removes_conversation_messages(client, tmp_path):
    create_account(client, "chat-cleanup@example.test")
    sign_in(client, "chat-cleanup@example.test")
    pdf = tmp_path / "chat-cleanup-loan.pdf"
    make(pdf)
    with pdf.open("rb") as stream:
        uploaded = client.post("/api/v1/documents", files={"file": (pdf.name, stream, "application/pdf")},
                               headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    document_id = uploaded.json()["id"]
    conversation = post_csrf(client, "/api/v1/conversations", {"document_id": document_id, "title": "Loan"}).json()
    post_csrf(client, f"/api/v1/conversations/{conversation['id']}/messages", {
        "question": "What is the loan amount?",
    })
    response = client.request("DELETE", "/api/v1/auth/me", json={
        "password": "correct horse battery staple 2026", "confirmation": "DELETE",
    }, headers={"X-CSRF-Token": client.cookies.get("bankease_csrf")})
    assert response.status_code == 204
    repository = AuthRepository(root=store.ROOT)
    assert repository.find_one("conversations", {"_id": conversation["id"]}) is None
    assert repository.find_many("messages", {"conversation_id": conversation["id"]}) == []
