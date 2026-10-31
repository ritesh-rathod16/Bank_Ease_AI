"""Durable, owner-scoped document-processing queue and worker loop."""
from datetime import datetime, timedelta, timezone
import logging
import os
import threading
import time
import uuid

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from . import store

LOGGER = logging.getLogger("bankease.document_jobs")
MAX_ATTEMPTS = max(1, int(os.getenv("BANKEASE_JOB_MAX_ATTEMPTS", "3")))
LEASE_SECONDS = max(60, int(os.getenv("BANKEASE_JOB_LEASE_SECONDS", "180")))


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat(timespec="seconds")


def enqueue(repository, owner_id: str, document_id: str):
    existing = repository.find_one("processing_jobs", {"_id": document_id, "owner_id": owner_id})
    if existing:
        return existing
    now = _now()
    job = {
        "_id": document_id,
        "owner_id": owner_id,
        "document_id": document_id,
        "idempotency_key": document_id,
        "status": "queued",
        "attempts": 0,
        "max_attempts": MAX_ATTEMPTS,
        "scheduled_at": _iso(now),
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "error": None,
    }
    try:
        repository.insert_one("processing_jobs", job)
    except DuplicateKeyError:
        existing = repository.find_one("processing_jobs", {"_id": document_id, "owner_id": owner_id})
        if existing:
            return existing
        raise
    return job


def enqueue_missing_jobs(repository):
    """Repair the document-saved/job-not-saved crash window on worker startup."""
    for owner_id, document_id in store.list_processing_documents():
        try:
            enqueue(repository, owner_id, document_id)
        except DuplicateKeyError:
            # Another API process inserted the idempotent job at the same time.
            continue


def public_job(job):
    if not job:
        return None
    return {key: job.get(key) for key in (
        "status", "attempts", "max_attempts", "scheduled_at", "updated_at", "error"
    )}


def retry(repository, owner_id: str, document_id: str):
    job = repository.find_one("processing_jobs", {"_id": document_id, "owner_id": owner_id})
    if not job:
        raise KeyError(document_id)
    if job.get("status") != "failed":
        return job
    now = _iso(_now())
    repository.update_one("processing_jobs", {"_id": document_id, "owner_id": owner_id, "status": "failed"}, {
        "status": "queued", "attempts": 0, "scheduled_at": now, "updated_at": now,
        "lease_expires_at": None, "worker_id": None, "error": None,
    })
    return repository.find_one("processing_jobs", {"_id": document_id, "owner_id": owner_id})


def remove_for_document(repository, owner_id: str, document_id: str):
    return repository.delete_many("processing_jobs", {"owner_id": owner_id, "document_id": document_id})


def _safe_error(error):
    if isinstance(error, HTTPException):
        value = error.detail if isinstance(error.detail, str) else "Document could not be processed."
    else:
        value = "Document processing failed unexpectedly. Retry the job; if it fails again, contact support."
    value = " ".join(str(value).split())
    return "".join(character for character in value if ord(character) >= 32)[:300]


def process_next(repository, worker_id="test-worker") -> bool:
    now = _now()
    job = repository.claim_processing_job(worker_id, _iso(now), _iso(now + timedelta(seconds=LEASE_SECONDS)))
    if job is None:
        return False
    document_id, owner_id = job["document_id"], job["owner_id"]
    if int(job.get("attempts", 0)) > int(job.get("max_attempts", MAX_ATTEMPTS)):
        error = "The worker stopped before this analysis could finish. Request a retry to queue it again."
        finished_at = _iso(_now())
        changed = repository.update_one("processing_jobs", {"_id": job["_id"], "owner_id": owner_id,
                                                              "worker_id": worker_id, "status": "processing"}, {
            "status": "failed", "error": error, "updated_at": finished_at,
            "finished_at": finished_at, "lease_expires_at": None,
        })
        if changed:
            try:
                record = store.load(document_id, owner_id)
                record.update(status="failed", error=error, updated_at=finished_at)
                store.save(record, owner_id)
            except KeyError:
                pass
        return True
    heartbeat_stop = threading.Event()
    lease_lost = threading.Event()

    def renew_lease():
        while not heartbeat_stop.wait(min(30, max(5, LEASE_SECONDS // 3))):
            stamp = _iso(_now())
            renewed = repository.update_one("processing_jobs", {
                "_id": job["_id"], "owner_id": owner_id, "worker_id": worker_id, "status": "processing",
            }, {"lease_expires_at": _iso(_now() + timedelta(seconds=LEASE_SECONDS)), "updated_at": stamp})
            if not renewed:
                lease_lost.set()
                return

    heartbeat = None
    if worker_id != "test-worker":
        heartbeat = threading.Thread(target=renew_lease, name=f"job-heartbeat-{document_id[:8]}", daemon=True)
        heartbeat.start()
    try:
        from . import document_processing

        pending = store.load(document_id, owner_id)
        if pending.get("status") not in {"processing", "failed"}:
            repository.update_one("processing_jobs", {"_id": job["_id"], "worker_id": worker_id}, {
                "status": "cancelled", "updated_at": _iso(_now()), "lease_expires_at": None,
            })
            return True
        if pending.get("status") == "failed":
            pending.update(status="processing", error=None, updated_at=_iso(_now()))
            store.save(pending, owner_id)
        source_path, _ = store.source_path(document_id, owner_id)
        source_bytes = source_path.read_bytes()
        result = document_processing.analyze_document_bytes(source_bytes, pending["filename"], document_id)
        result.update({key: pending[key] for key in (
            "created_at", "source_file", "version_group_id", "version_number", "previous_version_id",
            "supersedes_document_id", "processing_job_id",
        ) if key in pending})
        result["updated_at"] = _iso(_now())

        supersedes_id = pending.get("supersedes_document_id")
        if supersedes_id:
            parent = store.load(supersedes_id, owner_id)
            same_statement_family = (
                str(parent.get("doc_type", "")).startswith("bank_statement_")
                and str(result.get("doc_type", "")).startswith("bank_statement_")
            )
            same_document_type = (
                parent.get("doc_type") == result.get("doc_type")
                and parent.get("loan_category") == result.get("loan_category")
            )
            history = store.version_history(document_id, owner_id)
            if not (same_statement_family or same_document_type):
                raise HTTPException(409, "A revision must use the same document type or remain within the bank-statement family.")
            if history["versions"][-1]["id"] != document_id or pending.get("previous_version_id") != supersedes_id:
                raise HTTPException(409, "A newer version was added. Start from the latest document version.")

        lease = repository.find_one("processing_jobs", {
            "_id": job["_id"], "owner_id": owner_id, "worker_id": worker_id, "status": "processing",
        })
        if lease is None or lease_lost.is_set():
            return True  # Deleted/cancelled while the worker was processing.
        store.save(result, owner_id)
        still_owned = repository.find_one("processing_jobs", {
            "_id": job["_id"], "owner_id": owner_id, "worker_id": worker_id, "status": "processing",
        })
        if still_owned is None:
            try:
                store.delete(document_id, owner_id)
            except KeyError:
                pass
            return True
        repository.update_one("processing_jobs", {
            "_id": job["_id"], "owner_id": owner_id, "worker_id": worker_id, "status": "processing",
        }, {"status": "completed", "updated_at": result["updated_at"],
            "finished_at": result["updated_at"], "lease_expires_at": None, "error": None})
    except Exception as exc:
        error = _safe_error(exc)
        retryable = not isinstance(exc, HTTPException) or exc.status_code >= 500
        attempts = int(job.get("attempts", 1))
        final = not retryable or attempts >= int(job.get("max_attempts", MAX_ATTEMPTS))
        finished_at = _iso(_now())
        if final:
            changed = repository.update_one("processing_jobs", {
                "_id": job["_id"], "owner_id": owner_id, "worker_id": worker_id, "status": "processing",
            }, {"status": "failed", "updated_at": finished_at, "finished_at": finished_at,
                "lease_expires_at": None, "error": error})
            if changed:
                try:
                    record = store.load(document_id, owner_id)
                    record.update(status="failed", error=error, updated_at=finished_at)
                    store.save(record, owner_id)
                except KeyError:
                    pass
        else:
            delay = min(300, 5 * (2 ** max(0, attempts - 1)))
            repository.update_one("processing_jobs", {
                "_id": job["_id"], "owner_id": owner_id, "worker_id": worker_id, "status": "processing",
            }, {"status": "queued", "scheduled_at": _iso(_now() + timedelta(seconds=delay)),
                "updated_at": finished_at, "lease_expires_at": None, "worker_id": None, "error": error})
        LOGGER.warning("Document job %s failed on attempt %s (%s)", document_id, attempts, type(exc).__name__)
    finally:
        heartbeat_stop.set()
        if heartbeat is not None:
            heartbeat.join(timeout=1)
    return True


def run_worker(repository, stop_event: threading.Event, poll_seconds=0.5,
               retention_hours=24.0, retention_sweep_seconds=900):
    worker_id = f"{os.getpid()}-{uuid.uuid4().hex}"
    recovered = False
    next_retention_sweep = 0.0
    while not stop_event.is_set():
        try:
            if not recovered:
                enqueue_missing_jobs(repository)
                recovered = True
            monotonic_now = time.monotonic()
            if retention_hours > 0 and monotonic_now >= next_retention_sweep:
                from . import retention
                removed = retention.sweep_expired(repository, retention_hours)
                if removed:
                    LOGGER.info("Retention sweep removed %d expired document(s)", removed)
                next_retention_sweep = monotonic_now + retention_sweep_seconds
            worked = process_next(repository, worker_id)
        except Exception as exc:
            LOGGER.warning("Document worker poll failed (%s)", type(exc).__name__)
            worked = False
        if not worked:
            stop_event.wait(poll_seconds)
