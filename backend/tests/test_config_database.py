import pytest
from bson.decimal128 import Decimal128
from types import SimpleNamespace

from backend.app.config import ConfigurationError, load_settings
from backend.app import database, store


def test_settings_default_to_explicit_local_development_store():
    settings = load_settings({})
    assert settings.environment == "development"
    assert settings.storage_backend == "local-json"
    assert settings.retention_hours == 24
    assert settings.retention_sweep_seconds == 900


@pytest.mark.parametrize("values", [
    {"BANKEASE_RETENTION_HOURS": "-1"},
    {"BANKEASE_RETENTION_HOURS": "nan"},
    {"BANKEASE_RETENTION_SWEEP_SECONDS": "0"},
])
def test_retention_settings_reject_unsafe_values(values):
    with pytest.raises(ConfigurationError, match="RETENTION"):
        load_settings(values)


def test_mongodb_requires_uri():
    with pytest.raises(ConfigurationError, match="MONGODB_URI is required"):
        load_settings({"BANKEASE_STORAGE_BACKEND": "mongodb"})


def test_production_fails_closed_until_account_security_exists():
    with pytest.raises(ConfigurationError, match="Production startup remains disabled"):
        load_settings({
            "APP_ENV": "production",
            "BANKEASE_STORAGE_BACKEND": "mongodb",
            "MONGODB_URI": "mongodb://localhost:27017",
        })


class FakeCollection:
    def __init__(self):
        self.indexes = []

    def create_index(self, keys, **options):
        self.indexes.append((keys, options))
        return options["name"]


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def test_indexes_include_unique_email_and_expiring_session_tokens():
    db = FakeDatabase()
    database.create_indexes(db)
    users = db["users"].indexes
    sessions = db["sessions"].indexes
    assert any(keys == [("email_normalized", 1)] and opts.get("unique") for keys, opts in users)
    assert any(keys == [("expires_at", 1)] and opts.get("expireAfterSeconds") == 0
               for keys, opts in sessions)
    assert "document_chunks" in db.collections
    assert "statements" in db.collections and "transactions" in db.collections
    assert "comparisons" in db.collections
    assert any(keys == [("created_at", 1)] and options.get("name") == "ix_documents_retention_created"
               for keys, options in db["documents"].indexes)
    assert "processing_jobs" in db.collections
    job_indexes = db["processing_jobs"].indexes
    assert any(keys == [("owner_id", 1), ("status", 1), ("scheduled_at", 1)]
               for keys, _ in job_indexes)
    assert any(keys == [("owner_id", 1), ("idempotency_key", 1)] and options.get("unique")
               for keys, options in job_indexes)
    comparison_indexes = db["comparisons"].indexes
    assert any(keys == [("owner_id", 1), ("created_at", -1)] for keys, _ in comparison_indexes)
    assert any(keys == [("owner_id", 1), ("document_ids", 1)] for keys, _ in comparison_indexes)
    assert any(keys == [("owner_id", 1), ("updated_at", -1)] for keys, _ in db["conversations"].indexes)
    assert any(keys == [("owner_id", 1), ("conversation_id", 1), ("created_at", 1)]
               for keys, _ in db["messages"].indexes)
    assert any(keys == [("owner_id", 1), ("conversation_id", 1), ("request_id", 1), ("role", 1)]
               and options.get("unique") for keys, options in db["messages"].indexes)


class Cursor(list):
    def sort(self, key, direction):
        return Cursor(sorted(self, key=lambda row: row[key], reverse=direction < 0))
    def skip(self, count):
        return Cursor(self[count:])
    def limit(self, count):
        return Cursor(self[:count])


class MemoryCollection:
    def __init__(self):
        self.rows = []

    def replace_one(self, query, document, upsert=False):
        self.delete_one(query)
        self.rows.append(dict(document))

    def find_one(self, query):
        return next((dict(row) for row in self.rows if all(row.get(k) == v for k, v in query.items())), None)

    def insert_many(self, documents):
        self.rows.extend(dict(row) for row in documents)

    def find(self, query, projection=None):
        return Cursor(dict(row) for row in self.rows
                      if all((v in row.get(k, []) if isinstance(row.get(k), list) else row.get(k) == v)
                             for k, v in query.items()))

    def delete_one(self, query):
        before = len(self.rows)
        self.rows = [row for row in self.rows if not all(row.get(k) == v for k, v in query.items())]
        return SimpleNamespace(deleted_count=before - len(self.rows))

    def delete_many(self, query):
        def matches(row):
            for key, value in query.items():
                actual = row.get(key)
                if isinstance(value, dict) and "$in" in value:
                    if isinstance(actual, list):
                        if not any(item in value["$in"] for item in actual): return False
                    elif actual not in value["$in"]: return False
                elif actual != value:
                    return False
            return True
        before = len(self.rows)
        self.rows = [row for row in self.rows if not matches(row)]
        return SimpleNamespace(deleted_count=before - len(self.rows))

    def update_one(self, query, update):
        def lookup(row, path):
            current = row
            for piece in path.split("."):
                if not isinstance(current, dict) or piece not in current:
                    return None, False
                current = current[piece]
            return current, True

        def matches(row, criteria):
            for key, value in criteria.items():
                if key == "$or":
                    if not any(matches(row, branch) for branch in value):
                        return False
                    continue
                actual, exists = lookup(row, key)
                if isinstance(value, dict) and "$exists" in value:
                    if exists != value["$exists"]:
                        return False
                elif not exists or actual != value:
                    return False
            return True

        for row in self.rows:
            if not matches(row, query):
                continue
            for path, value in update.get("$set", {}).items():
                target = row
                pieces = path.split(".")
                for piece in pieces[:-1]:
                    target = target.setdefault(piece, {})
                target[pieces[-1]] = value
            for path, value in update.get("$inc", {}).items():
                target = row
                pieces = path.split(".")
                for piece in pieces[:-1]:
                    target = target.setdefault(piece, {})
                target[pieces[-1]] = target.get(pieces[-1], 0) + value
            for path, value in update.get("$push", {}).items():
                target = row
                pieces = path.split(".")
                for piece in pieces[:-1]:
                    target = target.setdefault(piece, {})
                target.setdefault(pieces[-1], []).append(value)
            return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class MemoryDocumentDatabase:
    def __init__(self):
        self.documents = MemoryCollection()
        self.document_chunks = MemoryCollection()
        self.document_versions = MemoryCollection()
        self.statements = MemoryCollection()
        self.transactions = MemoryCollection()
        self.comparisons = MemoryCollection()


def test_mongo_store_round_trip_keeps_page_text_in_owned_chunks(monkeypatch):
    db = MemoryDocumentDatabase()
    monkeypatch.setattr(store, "_MONGO_DB", db)
    record = {"id": "a" * 32, "filename": "offer.pdf", "status": "completed",
              "pages": ["page one", "page two"], "terms": {"loan_amount": {"value": 500000}}}

    store.save(record)
    saved = db.documents.rows[0]
    assert saved["owner_id"] == "local-workspace"
    assert "pages" not in saved
    assert isinstance(saved["terms"]["loan_amount"]["value"], Decimal128)
    assert [chunk["page"] for chunk in db.document_chunks.rows] == [1, 2]
    assert store.load(record["id"])["pages"] == ["page one", "page two"]

    store.delete(record["id"])
    with pytest.raises(KeyError):
        store.load(record["id"])
    assert db.document_chunks.rows == []


def test_mongo_term_correction_is_atomic_with_embedded_audit_history(monkeypatch):
    db = MemoryDocumentDatabase()
    monkeypatch.setattr(store, "_MONGO_DB", db)
    record = {
        "id": "c" * 32, "filename": "offer.pdf", "status": "completed",
        "doc_type": "loan_agreement", "pages": ["Loan Amount INR 500,000"],
        "terms": {"loan_amount": {"value": 500000, "raw": "INR 500,000", "page": 1,
                                    "evidence": "Loan Amount INR 500,000", "status": "extracted"}},
    }
    store.save(record, "owner-456")
    corrected = store.record_term_correction(
        record["id"], "owner-456", "loan_amount", 600000.0, "INR 600,000", "Checked signed offer", "owner-456"
    )
    assert corrected["value"] == 500000
    assert corrected["effective_value"] == 600000
    assert corrected["correction_revision"] == 1
    assert isinstance(db.documents.rows[0]["terms"]["loan_amount"]["effective_value"], Decimal128)
    assert len(db.documents.rows[0]["terms"]["loan_amount"]["correction_history"]) == 1


def test_mongo_term_correction_rejects_stale_revision(monkeypatch):
    db = MemoryDocumentDatabase()
    monkeypatch.setattr(store, "_MONGO_DB", db)
    record = {
        "id": "d" * 32, "filename": "offer.pdf", "status": "completed", "doc_type": "loan_agreement",
        "terms": {"loan_amount": {"value": 500000, "raw": "INR 500,000", "page": 1, "evidence": "Loan Amount INR 500,000", "status": "extracted"}},
    }
    store.save(record, "owner-789")
    update = db.documents.update_one

    def simulate_another_writer(query, changes):
        term = db.documents.rows[0]["terms"]["loan_amount"]
        term["effective_value"] = Decimal128("550000")
        term["correction_revision"] = 1
        return update(query, changes)

    monkeypatch.setattr(db.documents, "update_one", simulate_another_writer)
    with pytest.raises(ValueError, match="field changed"):
        store.record_term_correction(record["id"], "owner-789", "loan_amount", 600000.0,
                                     "INR 600,000", "Second correction", "owner-789")
    assert db.documents.rows[0]["terms"]["loan_amount"]["effective_value"] == Decimal128("550000")
    assert "correction_history" not in db.documents.rows[0]["terms"]["loan_amount"]


def test_mongo_saved_comparisons_are_owner_scoped_and_deleted_with_source(monkeypatch):
    db = MemoryDocumentDatabase()
    monkeypatch.setattr(store, "_MONGO_DB", db)
    first = {"id": "e" * 32, "filename": "first.pdf", "status": "completed", "terms": {}}
    second = {"id": "f" * 32, "filename": "second.pdf", "status": "completed", "terms": {}}
    store.save(first, "owner-mongo")
    store.save(second, "owner-mongo")
    saved = store.save_comparison({
        "id": "1" * 32, "kind": "loan", "title": "first vs second",
        "document_ids": [first["id"], second["id"]], "request": {"document_id_a": first["id"]},
        "summary": {"difference_count": 1}, "result": {"rows": []},
    }, "owner-mongo")
    assert saved["id"] == "1" * 32
    assert store.get_comparison(saved["id"], "owner-mongo")["result"] == {"rows": []}
    with pytest.raises(KeyError):
        store.get_comparison(saved["id"], "someone-else")
    assert store.list_comparisons("owner-mongo", limit=1)["comparisons"][0]["id"] == saved["id"]
    assert store.delete_comparisons_for_documents("owner-mongo", [first["id"]]) == 1
    with pytest.raises(KeyError):
        store.get_comparison(saved["id"], "owner-mongo")
    store.save_comparison({
        "id": "2" * 32, "kind": "loan", "title": "second snapshot",
        "document_ids": [first["id"], second["id"]], "request": {},
        "summary": {}, "result": {"rows": []},
    }, "owner-mongo")
    store.delete_owner("owner-mongo")
    assert db.comparisons.rows == []
    assert db.documents.rows == []
    assert db.document_versions.rows == []


@pytest.mark.parametrize("doc_type", ["bank_statement_csv", "bank_statement_pdf"])
def test_mongo_statement_storage_uses_owner_scoped_transaction_rows(monkeypatch, doc_type):
    db = MemoryDocumentDatabase()
    monkeypatch.setattr(store, "_MONGO_DB", db)
    record = {
        "id": "b" * 32, "filename": f"statement.{doc_type.removeprefix('bank_statement_')}", "status": "completed",
        "doc_type": doc_type, "transaction_count": 2, "currency": "INR",
        "statement_analysis": {"balance_check": "consistent", "opening_balance_minor": 0},
        "transactions": [
            {"row_number": 2, "date": "2026-01-01", "amount_minor": 100, "description": "Credit"},
            {"row_number": 3, "date": "2026-01-02", "amount_minor": -50, "description": "Debit"},
        ],
    }

    store.save(record, "owner-123")
    assert "transactions" not in db.documents.rows[0]
    assert all(row["owner_id"] == "owner-123" for row in db.transactions.rows)
    assert db.document_versions.rows[0]["document_id"] == record["id"]
    assert db.document_versions.rows[0]["version"] == 1
    history = store.version_history(record["id"], "owner-123")
    assert [version["version_number"] for version in history["versions"]] == [1]
    store.ensure_version_metadata(record["id"], "owner-123")
    assert db.documents.rows[0]["version_group_id"] == record["id"]
    assert db.document_versions.rows[0]["version"] == 1
    page = store.transaction_page(record["id"], "owner-123", offset=1, limit=1)
    assert page["total"] == 2
    assert page["transactions"][0]["row_number"] == 3
    assert page["statement_analysis"]["balance_check"] == "consistent"
    filtered = store.transaction_page(record["id"], "owner-123", search="credit", date_from="2026-01-01")
    assert filtered["total"] == 1 and filtered["transactions"][0]["description"] == "Credit"
    assert store.list_owner("owner-123")[0]["transaction_count"] == 2

    store.delete(record["id"], "owner-123")
    assert db.transactions.rows == []
    assert db.statements.rows == []


def test_mongo_statement_correction_is_atomic_owner_scoped_and_exported(monkeypatch):
    db = MemoryDocumentDatabase()
    monkeypatch.setattr(store, "_MONGO_DB", db)
    record = {
        "id": "9" * 32, "filename": "statement.csv", "status": "completed",
        "doc_type": "bank_statement_csv", "transaction_count": 1, "currency": "INR",
        "statement_analysis": {"balance_check": "consistent"},
        "transactions": [{
            "row_number": 2, "date": "2026-01-01", "description": "Rent",
            "debit_minor": 250000, "credit_minor": 0, "amount_minor": -250000,
            "balance_minor": 750000, "currency": "INR", "source_values": {"debit": "2500.00"},
        }],
    }
    store.save(record, "mongo-statement-owner")
    result = store.record_transaction_correction(
        record["id"], "mongo-statement-owner", 2,
        {"description": "Rent payment", "amount_minor": -255000},
        "Verified with the receipt.", "mongo-statement-owner", 0,
    )
    assert result["description"] == "Rent"
    assert result["effective"]["description"] == "Rent payment"
    assert result["effective"]["debit_minor"] == 255000
    assert result["source_values"]["debit"] == "2500.00"
    assert result["correction_revision"] == 1
    with pytest.raises(ValueError, match="changed while"):
        store.record_transaction_correction(
            record["id"], "mongo-statement-owner", 2, {"description": "stale"}, None,
            "mongo-statement-owner", 0,
        )
    assert store.export_transactions("mongo-statement-owner")[0]["correction_history"][0]["revision"] == 1
    assert store.export_transactions("other-owner") == []
