import copy, json, os, re, pathlib, time, threading, uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from bson.decimal128 import Decimal128
ROOT = pathlib.Path(os.getenv("BANKEASE_DATA", pathlib.Path(__file__).resolve().parents[2] / "data" / "runtime"))
ID = re.compile(r"^[0-9a-f]{32}$")
_MONGO_DB = None
_WORKSPACE_OWNER = "local-workspace"
_MONEY_FIELDS = {"loan_amount", "emi", "processing_fee", "late_fee", "sum_insured", "premium_amount"}
_SOURCE_EXTENSIONS = {"pdf", "csv", "png", "jpg", "jpeg", "tif", "tiff", "xlsx", "docx"}
_SOURCE_MEDIA_TYPES = {
    "pdf": "application/pdf", "csv": "text/csv", "png": "image/png",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "tif": "image/tiff",
    "tiff": "image/tiff", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_RECORD_LOCK = threading.RLock()

def configure_mongo(db=None):
    """Select the Mongo repository after the application has connected successfully."""
    global _MONGO_DB
    _MONGO_DB = db

def backend_name():
    return "mongodb" if _MONGO_DB is not None else "local-json"

def _p(i, ext):
    if not ID.match(i) or ext not in ({"json"} | _SOURCE_EXTENSIONS): raise KeyError(i)
    return ROOT / ("uploads" if ext in _SOURCE_EXTENSIONS else "records") / f"{i}.{ext}"

def save_source_file(i, extension, content):
    """Persist validated upload bytes under a generated, private storage key."""
    path = _p(i, extension)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(directory, 0o700)
    temporary = path.with_name(f".{i}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return {"storage_key": path.name, "extension": extension,
            "media_type": _SOURCE_MEDIA_TYPES.get(extension, "application/octet-stream"),
            "size_bytes": len(content)}

def _remove_source_file(i, reference):
    if not ID.match(i) or not isinstance(reference, dict):
        return False
    extension = reference.get("extension")
    if extension not in _SOURCE_EXTENSIONS or reference.get("storage_key") != f"{i}.{extension}":
        return False
    path = _p(i, extension)
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed

def remove_source_file(i, reference):
    return _remove_source_file(i, reference)

def source_path(i, owner_id):
    """Resolve a source path only after checking the parent record's owner."""
    record = load(i, owner_id)
    reference = record.get("source_file")
    if not isinstance(reference, dict):
        raise FileNotFoundError(i)
    extension = reference.get("extension")
    if extension not in _SOURCE_EXTENSIONS or reference.get("storage_key") != f"{i}.{extension}":
        raise FileNotFoundError(i)
    path = _p(i, extension)
    directory = ROOT / "uploads"
    if directory.is_symlink() or path.is_symlink() or not path.is_file():
        raise FileNotFoundError(i)
    try:
        if path.resolve(strict=True).parent != directory.resolve(strict=True):
            raise FileNotFoundError(i)
    except OSError as exc:
        raise FileNotFoundError(i) from exc
    # Never trust a persisted content type when serving an inline user upload.
    media_type = "text/plain; charset=utf-8" if extension == "csv" else _SOURCE_MEDIA_TYPES.get(extension, "application/octet-stream")
    return path, media_type

def _comparison_path(i):
    if not ID.match(i): raise KeyError(i)
    return ROOT / "comparisons" / f"{i}.json"

def _mongo_money(value):
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return Decimal128(Decimal(str(value)))
    return value

def _plain_money(value):
    return float(value.to_decimal()) if isinstance(value, Decimal128) else value

def _mongo_terms(terms):
    output = {name: dict(term) for name, term in terms.items()}
    for term in output.values():
        term.setdefault("effective_value", term.get("value"))
        term.setdefault("correction_revision", 0)
    for name in _MONEY_FIELDS:
        term = output.get(name)
        if not term:
            continue
        for key in ("value", "effective_value"):
            if key in term:
                term[key] = _mongo_money(term[key])
        correction = term.get("user_correction")
        if correction and "value" in correction:
            correction["value"] = _mongo_money(correction["value"])
        for event in term.get("correction_history", []):
            for key in ("before_value", "after_value"):
                if key in event:
                    event[key] = _mongo_money(event[key])
    return output

def _plain_terms(terms):
    output = {name: dict(term) for name, term in terms.items()}
    for name in _MONEY_FIELDS:
        term = output.get(name)
        if not term:
            continue
        for key in ("value", "effective_value"):
            if key in term:
                term[key] = _plain_money(term[key])
        correction = term.get("user_correction")
        if correction and "value" in correction:
            correction["value"] = _plain_money(correction["value"])
        for event in term.get("correction_history", []):
            for key in ("before_value", "after_value"):
                if key in event:
                    event[key] = _plain_money(event[key])
    return output

def save(rec, owner_id=_WORKSPACE_OWNER):
    if _MONGO_DB is not None:
        now = datetime.now(timezone.utc)
        is_statement = str(rec.get("doc_type", "")).startswith("bank_statement_")
        excluded = {"pages", "transactions", "statement_analysis"} if is_statement else {"pages"}
        document = {k: v for k, v in rec.items() if k not in excluded}
        document["terms"] = _mongo_terms(rec.get("terms", {}))
        document.update({"_id": rec["id"], "owner_id": owner_id, "schema_version": 1,
                         "created_at": rec.get("created_at", now), "updated_at": now})
        _MONGO_DB.documents.replace_one(
            {"_id": rec["id"], "owner_id": owner_id}, document, upsert=True
        )
        version_row = {
            "_id": rec["id"], "owner_id": owner_id,
            "document_id": rec.get("version_group_id", rec["id"]),
            "version": int(rec.get("version_number", 1)),
            "previous_document_id": rec.get("previous_version_id"),
            "filename": rec.get("filename"), "doc_type": rec.get("doc_type"),
            "status": rec.get("status"),
        }
        if is_statement:
            summary = dict(rec.get("statement_analysis", {}))
            summary.update({"_id": rec["id"], "document_id": rec["id"], "owner_id": owner_id})
            transaction_rows = [
                {**row, "_id": f"{rec['id']}:{row['row_number']}",
                 "document_id": rec["id"], "owner_id": owner_id}
                for row in rec.get("transactions", [])
            ]
            try:
                _MONGO_DB.document_versions.replace_one(
                    {"_id": rec["id"], "owner_id": owner_id}, version_row, upsert=True
                )
                _MONGO_DB.statements.replace_one(
                    {"_id": rec["id"], "owner_id": owner_id}, summary, upsert=True
                )
                _MONGO_DB.transactions.delete_many({"owner_id": owner_id, "document_id": rec["id"]})
                if transaction_rows:
                    _MONGO_DB.transactions.insert_many(transaction_rows)
            except Exception:
                _MONGO_DB.documents.delete_one({"_id": rec["id"], "owner_id": owner_id})
                _MONGO_DB.document_versions.delete_one({"_id": rec["id"], "owner_id": owner_id})
                _MONGO_DB.statements.delete_one({"_id": rec["id"], "owner_id": owner_id})
                _MONGO_DB.transactions.delete_many({"owner_id": owner_id, "document_id": rec["id"]})
                raise
            return
        _MONGO_DB.document_chunks.delete_many({"owner_id": owner_id, "document_id": rec["id"]})
        pages = rec.get("pages", [])
        try:
            _MONGO_DB.document_versions.replace_one(
                {"_id": rec["id"], "owner_id": owner_id}, version_row, upsert=True
            )
            if pages:
                _MONGO_DB.document_chunks.insert_many([
                    {"owner_id": owner_id, "document_id": rec["id"], "page": page, "text": text}
                    for page, text in enumerate(pages, 1)
                ])
        except Exception:
            _MONGO_DB.documents.delete_one({"_id": rec["id"], "owner_id": owner_id})
            _MONGO_DB.document_versions.delete_one({"_id": rec["id"], "owner_id": owner_id})
            _MONGO_DB.document_chunks.delete_many({"owner_id": owner_id, "document_id": rec["id"]})
            raise
        return
    d = ROOT / "records"; d.mkdir(parents=True, exist_ok=True, mode=0o700)
    f = _p(rec["id"], "json")
    owned = dict(rec)
    owned["terms"] = {name: dict(term) for name, term in rec.get("terms", {}).items()}
    for term in owned["terms"].values():
        term.setdefault("effective_value", term.get("value"))
        term.setdefault("correction_revision", 0)
    owned["owner_id"] = owner_id
    f.write_text(json.dumps(owned)); os.chmod(f, 0o600)

def load(i, owner_id=_WORKSPACE_OWNER):
    if not ID.match(i): raise KeyError(i)
    if _MONGO_DB is not None:
        document = _MONGO_DB.documents.find_one({"_id": i, "owner_id": owner_id})
        if document is None: raise KeyError(i)
        document.pop("_id", None)
        document.pop("owner_id", None)
        document.pop("created_at", None)
        document.pop("updated_at", None)
        document.pop("schema_version", None)
        document["terms"] = _plain_terms(document.get("terms", {}))
        if str(document.get("doc_type", "")).startswith("bank_statement_"):
            summary = _MONGO_DB.statements.find_one({"_id": i, "owner_id": owner_id})
            if summary:
                summary.pop("_id", None)
                summary.pop("owner_id", None)
                summary.pop("document_id", None)
                document["statement_analysis"] = summary
            document["transactions"] = []
        pages = _MONGO_DB.document_chunks.find(
            {"owner_id": owner_id, "document_id": i}
        ).sort("page", 1)
        if not str(document.get("doc_type", "")).startswith("bank_statement_"):
            document["pages"] = [page["text"] for page in pages]
        return document
    p = _p(i, "json")
    if not p.exists(): raise KeyError(i)
    record = json.loads(p.read_text())
    if record.get("owner_id") != owner_id: raise KeyError(i)
    record.pop("owner_id", None)
    return record

def delete(i, owner_id=_WORKSPACE_OWNER):
    record = load(i, owner_id)
    if _MONGO_DB is not None:
        _MONGO_DB.documents.delete_one({"_id": i, "owner_id": owner_id})
        _MONGO_DB.document_chunks.delete_many({"owner_id": owner_id, "document_id": i})
        _MONGO_DB.statements.delete_one({"_id": i, "owner_id": owner_id})
        _MONGO_DB.transactions.delete_many({"owner_id": owner_id, "document_id": i})
        _MONGO_DB.document_versions.delete_one({"_id": i, "owner_id": owner_id})
        delete_comparisons_for_documents(owner_id, [i])
        _remove_source_file(i, record.get("source_file"))
        return
    _p(i, "json").unlink(missing_ok=True)
    _remove_source_file(i, record.get("source_file"))
    delete_comparisons_for_documents(owner_id, [i])

def purge(hours, owner_id=_WORKSPACE_OWNER):
    """Delete owner records and associated uploads older than `hours` (0 or None disables)."""
    if not hours or hours <= 0: return 0
    if _MONGO_DB is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        expired = list(_MONGO_DB.documents.find(
            {"owner_id": owner_id, "created_at": {"$lt": cutoff}}, {"_id": 1, "source_file": 1}
        ))
        ids = [item["_id"] for item in expired]
        if not ids: return 0
        for item in expired:
            _remove_source_file(item["_id"], item.get("source_file"))
        _MONGO_DB.documents.delete_many({"_id": {"$in": ids}, "owner_id": owner_id})
        _MONGO_DB.document_chunks.delete_many({"document_id": {"$in": ids}, "owner_id": owner_id})
        _MONGO_DB.statements.delete_many({"document_id": {"$in": ids}, "owner_id": owner_id})
        _MONGO_DB.transactions.delete_many({"document_id": {"$in": ids}, "owner_id": owner_id})
        _MONGO_DB.document_versions.delete_many({"_id": {"$in": ids}, "owner_id": owner_id})
        delete_comparisons_for_documents(owner_id, ids)
        return len(ids)
    cutoff, n = time.time() - hours * 3600, 0
    removed_ids = set()
    directory = ROOT / "records"
    for f in (directory.glob("*.json") if directory.exists() else []):
        if not f.is_file() or f.stat().st_mtime >= cutoff:
            continue
        try:
            rec = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if rec.get("owner_id") == owner_id:
            _remove_source_file(f.stem, rec.get("source_file"))
            f.unlink()
            n += 1
            if ID.match(f.stem):
                removed_ids.add(f.stem)
    delete_comparisons_for_documents(owner_id, removed_ids)
    return n

def expired_documents(hours, now=None, owner_id=None, limit=100):
    """List a bounded batch of expired documents without deleting their data."""
    if not hours or hours <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=hours)
    cutoff_iso = cutoff.isoformat(timespec="seconds")
    bounded_limit = max(1, min(int(limit), 1000))
    expired = []

    if _MONGO_DB is not None:
        owner_match = {"owner_id": owner_id} if owner_id is not None else {}
        query = {"$or": [
            {**owner_match, "created_at": {"$lt": cutoff}},
            {**owner_match, "created_at": {"$lt": cutoff_iso}},
        ]}
        cursor = _MONGO_DB.documents.find(query, {"_id": 1, "owner_id": 1})
        for row in cursor:
            if row.get("_id") and row.get("owner_id"):
                expired.append({"id": row["_id"], "owner_id": row["owner_id"]})
                if len(expired) >= bounded_limit:
                    break
        return expired

    directory = ROOT / "records"
    for path in directory.glob("*.json") if directory.exists() else []:
        if not path.is_file() or not ID.match(path.stem):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            record_owner = record.get("owner_id", _WORKSPACE_OWNER)
            if owner_id is not None and record_owner != owner_id:
                continue
            created = record.get("created_at")
            timestamp = None
            if isinstance(created, datetime):
                timestamp = created
            elif isinstance(created, str):
                try:
                    timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except ValueError:
                    timestamp = None
            if timestamp is None:
                timestamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            elif timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if timestamp.astimezone(timezone.utc) < cutoff:
                expired.append({"id": path.stem, "owner_id": record_owner})
        except (OSError, json.JSONDecodeError):
            continue
    expired.sort(key=lambda item: item["id"])
    return expired[:bounded_limit]

def list_owner(owner_id):
    if _MONGO_DB is not None:
        records = []
        for row in _MONGO_DB.documents.find({"owner_id": owner_id}):
            row.pop("_id", None)
            row.pop("owner_id", None)
            row.pop("schema_version", None)
            row["terms"] = _plain_terms(row.get("terms", {}))
            records.append(row)
        return records
    else:
        records = ROOT / "records"
        ids = []
        for path in records.glob("*.json") if records.exists() else []:
            try:
                if json.loads(path.read_text()).get("owner_id") == owner_id:
                    ids.append(path.stem)
            except (OSError, json.JSONDecodeError):
                continue
    return [load(i, owner_id) for i in ids]


def list_processing_documents():
    """Internal worker recovery scan; never exposed through an API response."""
    if _MONGO_DB is not None:
        return [(row["owner_id"], row["_id"]) for row in
                _MONGO_DB.documents.find({"status": "processing"}, {"owner_id": 1})]
    pending = []
    records = ROOT / "records"
    for path in records.glob("*.json") if records.exists() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        owner_id = row.get("owner_id")
        document_id = row.get("id")
        if row.get("status") == "processing" and isinstance(owner_id, str) and isinstance(document_id, str):
            pending.append((owner_id, document_id))
    return pending


def version_history(i, owner_id):
    """Return an owner's document family without exposing storage paths or extracted text."""
    current = load(i, owner_id)
    group_id = current.get("version_group_id", i)
    if not isinstance(group_id, str) or not ID.match(group_id):
        group_id = i
    if _MONGO_DB is not None:
        rows = list(_MONGO_DB.documents.find({"owner_id": owner_id, "version_group_id": group_id}))
        if not any(row.get("_id") == group_id for row in rows):
            legacy_parent = _MONGO_DB.documents.find_one({"_id": group_id, "owner_id": owner_id})
            if legacy_parent:
                rows.append(legacy_parent)
        for row in rows:
            row.pop("owner_id", None)
            row.pop("source_file", None)
            row.pop("pages", None)
            row.pop("transactions", None)
            row.pop("terms", None)
            row.pop("_id", None)
        if not rows:
            rows = [current]
    else:
        owned = list_owner(owner_id)
        rows = [record for record in owned
                if record.get("version_group_id", record["id"]) == group_id]
        if not any(record.get("id") == group_id for record in rows):
            legacy_parent = next((record for record in owned if record.get("id") == group_id), None)
            if legacy_parent:
                rows.append(legacy_parent)
    versions = [{
        "id": row.get("id"),
        "filename": row.get("filename"),
        "version_number": int(row.get("version_number", 1)),
        "previous_version_id": row.get("previous_version_id"),
        "doc_type": row.get("doc_type"),
        "status": row.get("status"),
        "page_count": row.get("page_count", 0),
        "transaction_count": row.get("transaction_count"),
    } for row in rows]
    versions.sort(key=lambda row: (row["version_number"], row["id"] or ""))
    return {"version_group_id": group_id, "versions": versions}


def ensure_version_metadata(i, owner_id):
    """Backfill version 1 for older records before linking a new revision."""
    record = load(i, owner_id)
    group_id = record.get("version_group_id")
    version_number = record.get("version_number")
    if isinstance(group_id, str) and ID.match(group_id) and isinstance(version_number, int):
        return record
    metadata = {
        "version_group_id": i,
        "version_number": 1,
        "previous_version_id": None,
    }
    if _MONGO_DB is not None:
        _MONGO_DB.documents.update_one(
            {"_id": i, "owner_id": owner_id}, {"$set": metadata}
        )
        _MONGO_DB.document_versions.replace_one({"_id": i, "owner_id": owner_id}, {
            "_id": i, "owner_id": owner_id, "document_id": i, "version": 1,
            "previous_document_id": None, "filename": record.get("filename"),
            "doc_type": record.get("doc_type"), "status": record.get("status"),
        }, upsert=True)
        record.update(metadata)
    else:
        record.update(metadata)
        save(record, owner_id)
    return record


def transaction_page(i, owner_id, offset=0, limit=100, search="", date_from=None, date_to=None):
    """Return a filtered owner-scoped page of transactions and statement summary."""
    if not ID.match(i):
        raise KeyError(i)
    needle = search.strip().casefold()

    def matches(row):
        effective = row.get("effective") or row
        date = str(effective.get("date", row.get("date", "")))
        if date_from and date < date_from:
            return False
        if date_to and date > date_to:
            return False
        if needle:
            values = row.get("source_values", {})
            haystack = " ".join([
                date, str(effective.get("description", row.get("description", ""))),
                str(row.get("date", "")), str(row.get("description", "")), str(row.get("evidence", "")),
                *(str(value) for value in values.values()),
            ]).casefold()
            if needle not in haystack:
                return False
        return True

    if _MONGO_DB is None:
        record = load(i, owner_id)
        if not str(record.get("doc_type", "")).startswith("bank_statement_"):
            raise ValueError("Document is not an imported statement")
        transactions = [row for row in record.get("transactions", []) if matches(row)]
        return {
            "total": len(transactions),
            "offset": offset,
            "limit": limit,
            "transactions": transactions[offset:offset + limit],
            "statement_analysis": record.get("statement_analysis", {}),
        }
    document = _MONGO_DB.documents.find_one({"_id": i, "owner_id": owner_id})
    if not document:
        raise KeyError(i)
    if not str(document.get("doc_type", "")).startswith("bank_statement_"):
        raise ValueError("Document is not an imported statement")
    cursor = _MONGO_DB.transactions.find({"owner_id": owner_id, "document_id": i}).sort("row_number", 1)
    filtered_rows = [row for row in list(cursor) if matches(row)]
    transactions = []
    for row in filtered_rows[offset:offset + limit]:
        row.pop("_id", None)
        row.pop("owner_id", None)
        row.pop("document_id", None)
        transactions.append(row)
    summary = _MONGO_DB.statements.find_one({"_id": i, "owner_id": owner_id}) or {}
    summary.pop("_id", None)
    summary.pop("owner_id", None)
    summary.pop("document_id", None)
    return {
        "total": len(filtered_rows),
        "offset": offset,
        "limit": limit,
        "transactions": transactions,
        "statement_analysis": summary,
    }


def all_transactions(i, owner_id):
    """Load statement rows only after checking ownership of the parent document."""
    if not ID.match(i):
        raise KeyError(i)
    if _MONGO_DB is None:
        record = load(i, owner_id)
        if not str(record.get("doc_type", "")).startswith("bank_statement_"):
            raise ValueError("Document is not an imported statement")
        return record.get("transactions", [])
    document = _MONGO_DB.documents.find_one({"_id": i, "owner_id": owner_id})
    if not document:
        raise KeyError(i)
    if not str(document.get("doc_type", "")).startswith("bank_statement_"):
        raise ValueError("Document is not an imported statement")
    rows = list(_MONGO_DB.transactions.find(
        {"owner_id": owner_id, "document_id": i}
    ).sort("row_number", 1))
    for row in rows:
        row.pop("_id", None)
        row.pop("owner_id", None)
        row.pop("document_id", None)
    return rows


def export_transactions(owner_id):
    """Export normalized owner-owned statement rows, including corrections and history."""
    if _MONGO_DB is not None:
        rows = list(_MONGO_DB.transactions.find({"owner_id": owner_id}))
        for row in rows:
            for key in ("_id", "owner_id"):
                row.pop(key, None)
        rows.sort(key=lambda row: (row.get("document_id", ""), row.get("row_number", 0)))
        return rows
    rows = []
    for document in list_owner(owner_id):
        if not str(document.get("doc_type", "")).startswith("bank_statement_"):
            continue
        for transaction in document.get("transactions", []):
            rows.append({"document_id": document["id"], **copy.deepcopy(transaction)})
    rows.sort(key=lambda row: (row.get("document_id", ""), row.get("row_number", 0)))
    return rows


def record_transaction_correction(i, owner_id, row_number, changes, reason, actor_id, expected_revision):
    """Save owner-scoped effective row values without mutating extracted source values."""
    if not ID.match(i) or not isinstance(row_number, int) or isinstance(row_number, bool) or row_number < 1:
        raise KeyError(i)
    if not changes or set(changes) - {"date", "description", "amount_minor", "balance_minor"}:
        raise ValueError("Select at least one supported transaction field to correct.")

    def corrected_row(row):
        revision = int(row.get("correction_revision", 0))
        if revision != expected_revision:
            raise ValueError("This transaction changed while the correction was being saved. Reload and try again.")
        source_values = {field: row.get(field) for field in ("date", "description", "amount_minor", "balance_minor")}
        effective = dict(source_values)
        existing = row.get("user_correction") or {}
        correction_fields = dict(existing.get("fields", {}))
        before, after = {}, {}
        for field, value in changes.items():
            before[field] = (row.get("effective") or source_values).get(field)
            if value is None:
                correction_fields.pop(field, None)
                after[field] = source_values[field]
            else:
                correction_fields[field] = value
                after[field] = value
        effective.update(correction_fields)
        if "amount_minor" in correction_fields:
            amount_minor = effective["amount_minor"]
            effective["debit_minor"] = max(0, -amount_minor)
            effective["credit_minor"] = max(0, amount_minor)
        else:
            effective["debit_minor"] = row.get("debit_minor")
            effective["credit_minor"] = row.get("credit_minor")
        effective["currency"] = row.get("currency")
        event = {
            "id": uuid.uuid4().hex,
            "revision": revision + 1,
            "action": "revert" if all(value is None for value in changes.values()) else "set",
            "fields": dict(changes),
            "before": before,
            "after": after,
            "reason": reason,
            "actor_id": actor_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        correction = None if not correction_fields else {
            "fields": correction_fields,
            "reason": reason,
            "actor_id": actor_id,
            "updated_at": event["created_at"],
        }
        return effective, correction, event, revision

    with _RECORD_LOCK:
        if _MONGO_DB is not None:
            parent = _MONGO_DB.documents.find_one({"_id": i, "owner_id": owner_id})
            if not parent:
                raise KeyError(i)
            if not str(parent.get("doc_type", "")).startswith("bank_statement_"):
                raise ValueError("Document is not an imported statement")
            row = _MONGO_DB.transactions.find_one({
                "_id": f"{i}:{row_number}", "owner_id": owner_id,
                "document_id": i, "row_number": row_number,
            })
            if not row:
                raise KeyError(row_number)
            effective, correction, event, revision = corrected_row(row)
            revision_filter = {"correction_revision": expected_revision}
            if expected_revision == 0 and "correction_revision" not in row:
                revision_filter = {"correction_revision": {"$exists": False}}
            updated = _MONGO_DB.transactions.update_one(
                {"_id": row["_id"], "owner_id": owner_id, "document_id": i, **revision_filter},
                {"$set": {"effective": effective, "user_correction": correction,
                          "updated_at": datetime.now(timezone.utc)},
                 "$inc": {"correction_revision": 1}, "$push": {"correction_history": event}},
            )
            if updated.matched_count != 1:
                raise ValueError("This transaction changed while the correction was being saved. Reload and try again.")
            result = _MONGO_DB.transactions.find_one({"_id": row["_id"], "owner_id": owner_id})
            for key in ("_id", "owner_id", "document_id"):
                result.pop(key, None)
            return result

        record = load(i, owner_id)
        if not str(record.get("doc_type", "")).startswith("bank_statement_"):
            raise ValueError("Document is not an imported statement")
        row = next((item for item in record.get("transactions", []) if item.get("row_number") == row_number), None)
        if row is None:
            raise KeyError(row_number)
        effective, correction, event, revision = corrected_row(row)
        row.update({"effective": effective, "user_correction": correction,
                    "correction_revision": revision + 1})
        row.setdefault("correction_history", []).append(event)
        path = _p(i, "json")
        temporary = path.with_name(f".{i}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps({**record, "owner_id": owner_id}), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return row

def record_term_correction(i, owner_id, field, corrected_value, raw, reason, actor_id):
    """Persist an owner-scoped correction without replacing source extraction or evidence."""
    if not ID.match(i):
        raise KeyError(i)
    with _RECORD_LOCK:
        record = load(i, owner_id)
        if str(record.get("doc_type", "")).startswith("bank_statement_"):
            raise ValueError("Statement fields cannot be corrected through the loan-term editor")
        term = record.get("terms", {}).get(field)
        if term is None:
            raise KeyError(field)
        before = term.get("effective_value", term.get("value"))
        after = corrected_value if raw is not None else term.get("value")
        now = datetime.now(timezone.utc).isoformat()
        event = {
            "id": uuid.uuid4().hex,
            "revision": int(term.get("correction_revision", 0)) + 1,
            "action": "set" if raw is not None else "revert",
            "before_value": before,
            "after_value": after,
            "raw": raw,
            "reason": reason,
            "actor_id": actor_id,
            "created_at": now,
        }
        correction = None if raw is None else {
            "value": corrected_value,
            "raw": raw,
            "reason": reason,
            "actor_id": actor_id,
            "created_at": now,
        }
        updated_financial_checks = None
        if record.get("doc_type") == "financial_statement":
            candidate_terms = copy.deepcopy(record.get("terms", {}))
            candidate = candidate_terms[field]
            candidate["effective_value"] = after
            candidate["user_correction"] = correction
            from .financial_statements import verify_totals
            updated_financial_checks = verify_totals(candidate_terms)
        if _MONGO_DB is not None:
            correction_db = dict(correction) if correction is not None else None
            if correction_db and field in _MONEY_FIELDS:
                correction_db["value"] = _mongo_money(corrected_value)
            set_values = {
                f"terms.{field}.effective_value": _mongo_money(after) if field in _MONEY_FIELDS else after,
                f"terms.{field}.user_correction": correction_db,
                "updated_at": datetime.now(timezone.utc),
            }
            if updated_financial_checks is not None:
                set_values["financial_checks"] = updated_financial_checks
            event_db = dict(event)
            if field in _MONEY_FIELDS:
                for key in ("before_value", "after_value"):
                    event_db[key] = _mongo_money(event_db[key])
            updated = _MONGO_DB.documents.update_one(
                {
                    "_id": i,
                    "owner_id": owner_id,
                    "$or": [
                        {f"terms.{field}.correction_revision": int(term.get("correction_revision", 0))},
                        {f"terms.{field}.correction_revision": {"$exists": False},
                         f"terms.{field}.user_correction": {"$exists": False}},
                    ],
                },
                {"$set": set_values, "$inc": {f"terms.{field}.correction_revision": 1},
                 "$push": {f"terms.{field}.correction_history": event_db}},
            )
            if updated.matched_count != 1:
                raise ValueError("This field changed while the correction was being saved. Reload and try again.")
            return load(i, owner_id)["terms"][field]

        term["effective_value"] = after
        term["correction_revision"] = int(term.get("correction_revision", 0)) + 1
        term["user_correction"] = correction
        term.setdefault("correction_history", []).append(event)
        record["terms"][field] = term
        if updated_financial_checks is not None:
            record["financial_checks"] = updated_financial_checks
        directory = ROOT / "records"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = _p(i, "json")
        record["owner_id"] = owner_id
        temporary = path.with_name(f".{i}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(record))
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return {key: value for key, value in term.items() if key != "_id"}

def _comparison_out(record):
    output = copy.deepcopy(record)
    output["id"] = output.pop("_id", output.get("id"))
    output.pop("owner_id", None)
    for key in ("created_at", "updated_at"):
        if isinstance(output.get(key), datetime):
            value = output[key]
            output[key] = (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return output

def save_comparison(record, owner_id):
    """Persist a completed comparison after rechecking access to both source documents."""
    comparison_id = record.get("id")
    document_ids = record.get("document_ids", [])
    if not isinstance(comparison_id, str) or not ID.match(comparison_id):
        raise ValueError("Invalid comparison ID")
    if len(document_ids) != 2 or document_ids[0] == document_ids[1]:
        raise ValueError("A comparison must reference two different documents")
    for document_id in document_ids:
        load(document_id, owner_id)
    stored = copy.deepcopy(record)
    now = datetime.now(timezone.utc)
    if _MONGO_DB is not None:
        stored.pop("id", None)
        stored.update({"_id": comparison_id, "owner_id": owner_id,
                       "created_at": now, "updated_at": now, "schema_version": 1})
        _MONGO_DB.comparisons.replace_one(
            {"_id": comparison_id, "owner_id": owner_id}, stored, upsert=True
        )
        saved_created_at = now.isoformat()
    else:
        directory = ROOT / "comparisons"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = _comparison_path(comparison_id)
        stored.update({"owner_id": owner_id, "created_at": record.get("created_at", now.isoformat())})
        temporary = path.with_name(f".{comparison_id}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(stored))
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        saved_created_at = stored["created_at"]
    try:
        for document_id in document_ids:
            load(document_id, owner_id)
    except KeyError:
        try:
            delete_comparison(comparison_id, owner_id)
        except KeyError:
            pass
        raise
    return {"id": comparison_id, "created_at": saved_created_at}

def get_comparison(i, owner_id):
    if not ID.match(i):
        raise KeyError(i)
    if _MONGO_DB is not None:
        record = _MONGO_DB.comparisons.find_one({"_id": i, "owner_id": owner_id})
        if record is None:
            raise KeyError(i)
    else:
        path = _comparison_path(i)
        if not path.exists():
            raise KeyError(i)
        record = json.loads(path.read_text())
        if record.get("owner_id") != owner_id:
            raise KeyError(i)
    record = _comparison_out(record)
    for document_id in record.get("document_ids", []):
        load(document_id, owner_id)
    return record

def list_comparisons(owner_id, offset=0, limit=20, kind=None):
    if _MONGO_DB is not None:
        query = {"owner_id": owner_id}
        if kind:
            query["kind"] = kind
        rows = list(_MONGO_DB.comparisons.find(query).sort("created_at", -1).skip(offset).limit(limit + 1))
    else:
        directory = ROOT / "comparisons"
        rows = []
        for path in directory.glob("*.json") if directory.exists() else []:
            try:
                row = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if row.get("owner_id") == owner_id and (kind is None or row.get("kind") == kind):
                rows.append(row)
        rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        rows = rows[offset:offset + limit + 1]
    has_more = len(rows) > limit
    result = []
    for row in rows[:limit]:
        item = _comparison_out(row)
        item.pop("result", None)
        item.pop("request", None)
        item.pop("schema_version", None)
        result.append(item)
    return {"comparisons": result, "offset": offset, "limit": limit, "has_more": has_more}

def export_comparisons(owner_id):
    if _MONGO_DB is not None:
        records = [_comparison_out(row) for row in _MONGO_DB.comparisons.find(
            {"owner_id": owner_id}
        ).sort("created_at", -1)]
    else:
        directory = ROOT / "comparisons"
        records = []
        for path in directory.glob("*.json") if directory.exists() else []:
            try:
                row = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if row.get("owner_id") == owner_id:
                records.append(_comparison_out(row))
        records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return records

def delete_comparison(i, owner_id):
    if not ID.match(i):
        raise KeyError(i)
    if _MONGO_DB is not None:
        result = _MONGO_DB.comparisons.delete_one({"_id": i, "owner_id": owner_id})
        if result.deleted_count != 1:
            raise KeyError(i)
        return
    path = _comparison_path(i)
    if not path.exists():
        raise KeyError(i)
    record = json.loads(path.read_text())
    if record.get("owner_id") != owner_id:
        raise KeyError(i)
    path.unlink()

def _delete_local_comparisons(owner_id, document_ids=None):
    directory = ROOT / "comparisons"
    removed = 0
    for path in directory.glob("*.json") if directory.exists() else []:
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("owner_id") != owner_id:
            continue
        if document_ids is not None and not set(record.get("document_ids", [])) & set(document_ids):
            continue
        path.unlink(missing_ok=True)
        removed += 1
    return removed

def delete_comparisons_for_documents(owner_id, document_ids):
    ids = list(set(document_ids))
    if not ids:
        return 0
    if _MONGO_DB is not None:
        result = _MONGO_DB.comparisons.delete_many({
            "owner_id": owner_id, "document_ids": {"$in": ids}
        })
        return result.deleted_count
    return _delete_local_comparisons(owner_id, ids)

def delete_owner(owner_id):
    if _MONGO_DB is not None:
        records = list(_MONGO_DB.documents.find({"owner_id": owner_id}, {"_id": 1, "source_file": 1}))
        ids = [row["_id"] for row in records]
        for row in records:
            _remove_source_file(row["_id"], row.get("source_file"))
        _MONGO_DB.documents.delete_many({"owner_id": owner_id})
        if ids:
            _MONGO_DB.document_chunks.delete_many({"owner_id": owner_id, "document_id": {"$in": ids}})
            _MONGO_DB.statements.delete_many({"owner_id": owner_id, "document_id": {"$in": ids}})
            _MONGO_DB.transactions.delete_many({"owner_id": owner_id, "document_id": {"$in": ids}})
            _MONGO_DB.document_versions.delete_many({"owner_id": owner_id, "_id": {"$in": ids}})
        _MONGO_DB.comparisons.delete_many({"owner_id": owner_id})
        return len(ids)
    records = list_owner(owner_id)
    for record in records:
        delete(record["id"], owner_id)
    _delete_local_comparisons(owner_id)
    return len(records)
