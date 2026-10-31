"""Small repository for accounts and sessions, backed by MongoDB or local dev JSON."""
import json
import os
import pathlib
import threading
from copy import deepcopy
from datetime import datetime
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


COLLECTIONS = {
    "users", "sessions", "email_verifications", "password_resets", "user_preferences",
    "review_tasks", "audit_events", "processing_jobs", "conversations", "messages"
}
_LOCK = threading.RLock()


class DuplicateEmailError(ValueError):
    pass


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
            continue
        actual = document.get(key)
        if isinstance(expected, dict):
            for operator, value in expected.items():
                if operator == "$lt":
                    compare_value = value.isoformat() if isinstance(value, datetime) else value
                    if not (actual is not None and actual < compare_value):
                        return False
                if operator == "$lte":
                    compare_value = value.isoformat() if isinstance(value, datetime) else value
                    if not (actual is not None and actual <= compare_value):
                        return False
                if operator == "$in" and actual not in value:
                    return False
                if operator == "$ne" and actual == value:
                    return False
        elif isinstance(actual, list) and not isinstance(expected, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


class AuthRepository:
    """MongoDB in production-like local setups; atomic JSON files for local development."""

    def __init__(self, db=None, root=None):
        self.db = db
        self.root = pathlib.Path(root or pathlib.Path(__file__).resolve().parents[2] / "data" / "runtime")

    def _collection(self, name):
        if name not in COLLECTIONS:
            raise ValueError("Unsupported authentication collection")
        return self.db[name] if self.db is not None else None

    def _path(self, name):
        return self.root / "auth" / f"{name}.json"

    def _read(self, name):
        path = self._path(name)
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, name, documents):
        directory = self._path(name).parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self._path(name)
        temporary = target.with_suffix(".tmp")
        file_descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            json.dump(documents, stream, separators=(",", ":"),
                      default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)

    def find_one(self, name, query):
        collection = self._collection(name)
        if collection is not None:
            return collection.find_one(query)
        with _LOCK:
            return next((deepcopy(row) for row in self._read(name) if _matches(row, query)), None)

    def find_many(self, name, query=None, sort_field=None, descending=False):
        query = query or {}
        collection = self._collection(name)
        if collection is not None:
            cursor = collection.find(query)
            if sort_field:
                cursor = cursor.sort(sort_field, -1 if descending else 1)
            return list(cursor)
        with _LOCK:
            rows = [deepcopy(row) for row in self._read(name) if _matches(row, query)]
        if sort_field:
            rows.sort(key=lambda row: row.get(sort_field), reverse=descending)
        return rows

    def create_user(self, user):
        collection = self._collection("users")
        if collection is not None:
            try:
                collection.insert_one(user)
            except Exception as exc:
                if getattr(exc, "code", None) == 11000:
                    raise DuplicateEmailError() from exc
                raise
            return
        with _LOCK:
            rows = self._read("users")
            if any(row.get("email_normalized") == user["email_normalized"] for row in rows):
                raise DuplicateEmailError()
            rows.append(user)
            self._write("users", rows)

    def insert_one(self, name, document):
        collection = self._collection(name)
        if collection is not None:
            collection.insert_one(document)
            return
        with _LOCK:
            rows = self._read(name)
            rows.append(document)
            self._write(name, rows)

    def insert_message_once(self, document):
        """Insert an idempotent chat message, returning its stored row and whether it was new."""
        request_id = document.get("request_id")
        if not request_id:
            self.insert_one("messages", document)
            return deepcopy(document), True
        query = {key: document[key] for key in ("owner_id", "conversation_id", "request_id", "role")}
        collection = self._collection("messages")
        if collection is not None:
            try:
                collection.insert_one(document)
                return deepcopy(document), True
            except DuplicateKeyError:
                existing = collection.find_one(query)
                if existing is None:
                    raise
                return existing, False
        with _LOCK:
            rows = self._read("messages")
            existing = next((row for row in rows if _matches(row, query)), None)
            if existing is not None:
                return deepcopy(existing), False
            rows.append(document)
            self._write("messages", rows)
        return deepcopy(document), True

    def update_one(self, name, query, updates):
        collection = self._collection(name)
        if collection is not None:
            return collection.update_one(query, {"$set": updates}).modified_count > 0
        changed = False
        with _LOCK:
            rows = self._read(name)
            for row in rows:
                if _matches(row, query):
                    row.update(updates)
                    changed = True
                    break
            if changed:
                self._write(name, rows)
        return changed

    def delete_one(self, name, query):
        collection = self._collection(name)
        if collection is not None:
            return collection.delete_one(query).deleted_count > 0
        changed = False
        with _LOCK:
            rows = self._read(name)
            retained = []
            for row in rows:
                if not changed and _matches(row, query):
                    changed = True
                else:
                    retained.append(row)
            if changed:
                self._write(name, retained)
        return changed

    def delete_many(self, name, query):
        collection = self._collection(name)
        if collection is not None:
            return collection.delete_many(query).deleted_count
        with _LOCK:
            rows = self._read(name)
            retained = [row for row in rows if not _matches(row, query)]
            count = len(rows) - len(retained)
            if count:
                self._write(name, retained)
        return count

    def claim_processing_job(self, worker_id, now, lease_until):
        """Atomically lease one due job, or reclaim a job whose worker lease expired."""
        collection = self._collection("processing_jobs")
        lease = {"worker_id": worker_id, "lease_expires_at": lease_until,
                 "status": "processing", "updated_at": now}
        if collection is not None:
            for query in (
                {"status": "queued", "scheduled_at": {"$lte": now}},
                {"status": "processing", "lease_expires_at": {"$lte": now}},
            ):
                job = collection.find_one_and_update(
                    query, {"$set": lease, "$inc": {"attempts": 1}},
                    sort=[("scheduled_at", 1)], return_document=ReturnDocument.AFTER,
                )
                if job is not None:
                    return job
            return None
        with _LOCK:
            rows = self._read("processing_jobs")
            candidates = [row for row in rows if
                          (row.get("status") == "queued" and row.get("scheduled_at", "") <= now)
                          or (row.get("status") == "processing" and row.get("lease_expires_at", "") <= now)]
            if not candidates:
                return None
            job = min(candidates, key=lambda row: row.get("scheduled_at", ""))
            job.update(lease)
            job["attempts"] = int(job.get("attempts", 0)) + 1
            self._write("processing_jobs", rows)
            return deepcopy(job)
