import threading
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app import jobs, retention, store
from backend.app.auth_store import AuthRepository
from backend.app.jobs import enqueue


def _document(document_id, created_at, source_file):
    return {
        "id": document_id,
        "filename": f"{document_id}.pdf",
        "status": "processing",
        "doc_type": "pending",
        "terms": {},
        "pages": [],
        "clauses": [],
        "created_at": created_at.isoformat(),
        "source_file": source_file,
        "processing_job_id": document_id,
    }


def test_retention_sweep_cascades_owner_data_and_keeps_fresh_records(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    repository = AuthRepository(root=tmp_path)
    now = datetime.now(timezone.utc)
    expired_at = now - timedelta(hours=30)
    owner = "retention-owner"
    other_owner = "other-retention-owner"
    expired_id, other_id, fresh_id = "a" * 32, "b" * 32, "c" * 32

    expired_source = store.save_source_file(expired_id, "pdf", b"%PDF expired")
    other_source = store.save_source_file(other_id, "pdf", b"%PDF other")
    fresh_source = store.save_source_file(fresh_id, "pdf", b"%PDF fresh")
    store.save(_document(expired_id, expired_at, expired_source), owner)
    store.save(_document(other_id, expired_at, other_source), other_owner)
    store.save(_document(fresh_id, now, fresh_source), owner)

    enqueue(repository, owner, expired_id)
    conversation_id, message_id, task_id = "d" * 32, "e" * 32, "f" * 32
    repository.insert_one("conversations", {
        "_id": conversation_id, "owner_id": owner, "document_id": expired_id,
        "title": "Review this document", "created_at": now.isoformat(), "updated_at": now.isoformat(),
    })
    repository.insert_one("messages", {
        "_id": message_id, "owner_id": owner, "conversation_id": conversation_id,
        "role": "assistant", "content": "Evidence-backed answer", "created_at": now.isoformat(),
    })
    repository.insert_one("review_tasks", {
        "_id": task_id, "owner_id": owner, "document_id": expired_id, "status": "submitted",
    })
    repository.insert_one("audit_events", {
        "_id": "1" * 32, "owner_id": owner, "target_id": task_id, "action": "review_requested",
    })

    assert retention.sweep_expired(repository, 24, owner_id=owner, now=now) == 1
    assert repository.find_one("processing_jobs", {"owner_id": owner, "document_id": expired_id}) is None
    assert repository.find_one("conversations", {"_id": conversation_id}) is None
    assert repository.find_one("messages", {"_id": message_id}) is None
    assert repository.find_one("review_tasks", {"_id": task_id}) is None
    assert repository.find_one("audit_events", {"target_id": task_id}) is None
    assert not (tmp_path / "uploads" / f"{expired_id}.pdf").exists()
    assert store.load(fresh_id, owner)["id"] == fresh_id
    assert (tmp_path / "uploads" / f"{fresh_id}.pdf").is_file()
    assert store.load(other_id, other_owner)["id"] == other_id

    assert retention.sweep_expired(repository, 24, now=now) == 1
    assert not (tmp_path / "uploads" / f"{other_id}.pdf").exists()


def test_worker_runs_retention_sweep_on_start(monkeypatch):
    stop_event = threading.Event()
    called = []

    monkeypatch.setattr(jobs, "enqueue_missing_jobs", lambda repository: None)
    monkeypatch.setattr(jobs, "process_next", lambda repository, worker_id: False)

    def sweep(repository, hours):
        called.append(hours)
        stop_event.set()
        return 0

    monkeypatch.setattr(retention, "sweep_expired", sweep)
    jobs.run_worker(object(), stop_event, poll_seconds=0, retention_hours=12,
                    retention_sweep_seconds=900)
    assert called == [12]


def test_api_lifespan_starts_worker_with_retention_settings(tmp_path, monkeypatch):
    from backend.app import main

    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("BANKEASE_STORAGE_BACKEND", "local-json")
    monkeypatch.setenv("BANKEASE_ENABLE_DOCUMENT_WORKER", "true")
    monkeypatch.setenv("BANKEASE_RETENTION_HOURS", "12")
    monkeypatch.setenv("BANKEASE_RETENTION_SWEEP_SECONDS", "180")

    with TestClient(main.app):
        worker_thread = main.app.state.document_worker_thread
        assert worker_thread.is_alive()
        assert main.app.state.settings.retention_hours == 12
        assert main.app.state.settings.retention_sweep_seconds == 180


def test_retention_cancels_in_flight_worker_without_resurrecting_document(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    repository = AuthRepository(root=tmp_path)
    owner, document_id = "in-flight-owner", "9" * 32
    now = datetime.now(timezone.utc)
    source = store.save_source_file(document_id, "pdf", b"%PDF bounded source")
    store.save(_document(document_id, now - timedelta(hours=30), source), owner)
    enqueue(repository, owner, document_id)

    analysis_started, resume_analysis = threading.Event(), threading.Event()

    def analyze_document_bytes(data, filename, current_id):
        assert data == b"%PDF bounded source"
        analysis_started.set()
        assert resume_analysis.wait(5)
        return {
            "id": current_id, "filename": filename, "status": "completed", "terms": {},
            "pages": ["extracted text"], "clauses": [], "doc_type": "unknown",
            "page_count": 1, "unreadable_pages": [], "ocr_pages": [],
        }

    monkeypatch.setattr("backend.app.document_processing.analyze_document_bytes", analyze_document_bytes)
    result = []
    worker = threading.Thread(target=lambda: result.append(jobs.process_next(repository)))
    worker.start()
    assert analysis_started.wait(5)

    assert retention.sweep_expired(repository, 24, owner_id=owner, now=now) == 1
    resume_analysis.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert result == [True]
    assert repository.find_one("processing_jobs", {"owner_id": owner, "document_id": document_id}) is None
    assert not (tmp_path / "uploads" / f"{document_id}.pdf").exists()
    try:
        store.load(document_id, owner)
    except KeyError:
        pass
    else:
        raise AssertionError("The worker restored a document after retention deleted it")


def test_retention_disabled_does_not_scan(monkeypatch):
    monkeypatch.setattr(store, "expired_documents", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    assert retention.sweep_expired(AuthRepository(), 0) == 0


def test_mongo_expiration_query_handles_string_and_datetime_timestamps(monkeypatch):
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=30)

    class Documents:
        rows = [
            {"_id": "a" * 32, "owner_id": "owner-a", "created_at": old.isoformat()},
            {"_id": "b" * 32, "owner_id": "owner-a", "created_at": old},
            {"_id": "c" * 32, "owner_id": "owner-b", "created_at": old.isoformat()},
            {"_id": "d" * 32, "owner_id": "owner-a", "created_at": now.isoformat()},
        ]

        def find(self, query, projection):
            branches = query["$or"]
            assert projection == {"_id": 1, "owner_id": 1}
            assert isinstance(branches[0]["created_at"]["$lt"], datetime)
            assert isinstance(branches[1]["created_at"]["$lt"], str)

            def matches(row):
                for branch in branches:
                    if "owner_id" in branch and row["owner_id"] != branch["owner_id"]:
                        continue
                    cutoff = branch["created_at"]["$lt"]
                    created = row["created_at"]
                    if isinstance(created, type(cutoff)) and created < cutoff:
                        return True
                return False

            return [row for row in self.rows if matches(row)]

    class Database:
        documents = Documents()

    monkeypatch.setattr(store, "_MONGO_DB", Database())
    assert store.expired_documents(24, now=now, owner_id="owner-a") == [
        {"id": "a" * 32, "owner_id": "owner-a"},
        {"id": "b" * 32, "owner_id": "owner-a"},
    ]
