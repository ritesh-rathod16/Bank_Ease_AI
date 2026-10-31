"""Owner-scoped retention cleanup for documents and all dependent records."""
import logging

from . import conversations, store

LOGGER = logging.getLogger("bankease.retention")


def delete_document_data(repository, owner_id: str, document_id: str) -> bool:
    """Cancel active processing before deleting a document and its dependents."""
    repository.delete_many("processing_jobs", {"owner_id": owner_id, "document_id": document_id})
    existed = True
    try:
        store.delete(document_id, owner_id)
    except KeyError:
        existed = False

    conversations.delete_for_document(repository, owner_id, document_id)
    tasks = repository.find_many("review_tasks", {"owner_id": owner_id, "document_id": document_id})
    repository.delete_many("review_tasks", {"owner_id": owner_id, "document_id": document_id})
    for task in tasks:
        repository.delete_many("audit_events", {"owner_id": owner_id, "target_id": task["_id"]})
    return existed


def sweep_expired(repository, hours: float, owner_id: str | None = None,
                  now=None, limit: int = 100) -> int:
    """Delete a bounded batch of expired documents and owner-scoped derivatives."""
    if not hours or hours <= 0:
        return 0
    expired = store.expired_documents(hours, now=now, owner_id=owner_id, limit=limit)
    removed = 0
    for reference in expired:
        try:
            if delete_document_data(repository, reference["owner_id"], reference["id"]):
                removed += 1
        except Exception as exc:
            LOGGER.warning("Retention cleanup failed for a document (%s)", type(exc).__name__)
    return removed
