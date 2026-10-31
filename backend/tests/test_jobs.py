from datetime import datetime, timedelta, timezone

from backend.app import jobs, store
from backend.app.auth_store import AuthRepository
from backend.app.jobs import enqueue


def test_local_queue_claims_once_and_reclaims_an_expired_worker_lease(tmp_path):
    repository = AuthRepository(root=tmp_path)
    owner = "owner-123"
    first_id = "a" * 32
    second_id = "b" * 32
    enqueue(repository, owner, first_id)
    enqueue(repository, owner, second_id)
    now = datetime.now(timezone.utc)
    now_text = now.isoformat(timespec="seconds")
    lease_text = (now + timedelta(minutes=10)).isoformat(timespec="seconds")

    first = repository.claim_processing_job("worker-one", now_text, lease_text)
    second = repository.claim_processing_job("worker-two", now_text, lease_text)
    assert first["_id"] == first_id and first["attempts"] == 1
    assert second["_id"] == second_id and second["attempts"] == 1
    assert repository.claim_processing_job("worker-three", now_text, lease_text) is None

    recovered = repository.claim_processing_job(
        "worker-restart", (now + timedelta(minutes=11)).isoformat(timespec="seconds"),
        (now + timedelta(minutes=21)).isoformat(timespec="seconds"),
    )
    assert recovered["_id"] == first_id
    assert recovered["attempts"] == 2
    assert recovered["worker_id"] == "worker-restart"


def test_worker_start_recovers_processing_records_without_a_job(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    document_id = "c" * 32
    owner_id = "owner-recovery"
    store.save({
        "id": document_id, "filename": "orphaned-upload.pdf", "status": "processing",
        "doc_type": "pending", "terms": {}, "pages": [], "clauses": [],
        "processing_job_id": document_id,
    }, owner_id)
    repository = AuthRepository(root=tmp_path)

    jobs.enqueue_missing_jobs(repository)
    jobs.enqueue_missing_jobs(repository)

    recovered = repository.find_one("processing_jobs", {"_id": document_id, "owner_id": owner_id})
    assert recovered["status"] == "queued"
    assert recovered["document_id"] == document_id
    assert len(repository.find_many("processing_jobs", {"owner_id": owner_id})) == 1
