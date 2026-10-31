"""MongoDB connection lifecycle and indexes; repositories own query semantics."""
from pymongo import MongoClient


INDEXES = {
    "users": [([("email_normalized", 1)], {"unique": True, "name": "uq_users_email"})],
    "sessions": [
        ([ ("token_hash", 1)], {"unique": True, "name": "uq_sessions_token_hash"}),
        ([("expires_at", 1)], {"expireAfterSeconds": 0, "name": "ttl_sessions_expiry"}),
        ([("owner_id", 1), ("revoked_at", 1)], {"name": "ix_sessions_owner_revocation"}),
    ],
    "email_verifications": [
        ([ ("token_hash", 1)], {"unique": True, "name": "uq_email_verifications_token_hash"}),
        ([("expires_at", 1)], {"expireAfterSeconds": 0, "name": "ttl_email_verifications_expiry"}),
    ],
    "password_resets": [
        ([ ("token_hash", 1)], {"unique": True, "name": "uq_password_resets_token_hash"}),
        ([("expires_at", 1)], {"expireAfterSeconds": 0, "name": "ttl_password_resets_expiry"}),
    ],
    "documents": [
        ([ ("created_at", 1)], {"name": "ix_documents_retention_created"}),
        ([ ("owner_id", 1), ("created_at", -1)], {"name": "ix_documents_owner_created"}),
        ([ ("owner_id", 1), ("status", 1), ("updated_at", -1)], {"name": "ix_documents_owner_status_updated"}),
    ],
    "document_chunks": [
        ([ ("owner_id", 1), ("document_id", 1), ("page", 1)], {"unique": True, "name": "uq_chunks_owner_document_page"}),
    ],
    "statements": [
        ([("owner_id", 1), ("document_id", 1)], {"unique": True, "name": "uq_statements_owner_document"}),
    ],
    "transactions": [
        ([("owner_id", 1), ("document_id", 1), ("row_number", 1)], {"unique": True, "name": "uq_transactions_owner_document_row"}),
        ([("owner_id", 1), ("date", 1)], {"name": "ix_transactions_owner_date"}),
    ],
    "document_versions": [
        ([ ("owner_id", 1), ("document_id", 1), ("version", 1)], {"unique": True, "name": "uq_versions_owner_document_version"}),
    ],
    "extracted_fields": [
        ([ ("owner_id", 1), ("document_id", 1), ("field_name", 1)], {"name": "ix_fields_owner_document_name"}),
    ],
    "analyses": [
        ([ ("owner_id", 1), ("document_id", 1), ("created_at", -1)], {"name": "ix_analyses_owner_document_created"}),
    ],
    "comparisons": [
        ([ ("owner_id", 1), ("updated_at", -1)], {"name": "ix_comparisons_owner_updated"}),
        ([ ("owner_id", 1), ("created_at", -1)], {"name": "ix_comparisons_owner_created"}),
        ([ ("owner_id", 1), ("document_ids", 1)], {"name": "ix_comparisons_owner_documents"}),
    ],
    "conversations": [
        ([ ("owner_id", 1), ("updated_at", -1)], {"name": "ix_conversations_owner_updated"}),
        ([ ("owner_id", 1), ("document_ids", 1)], {"name": "ix_conversations_owner_documents"}),
    ],
    "messages": [
        ([ ("owner_id", 1), ("conversation_id", 1), ("created_at", 1)], {"name": "ix_messages_owner_conversation_created"}),
        ([("owner_id", 1), ("conversation_id", 1), ("request_id", 1), ("role", 1)],
         {"unique": True, "partialFilterExpression": {"request_id": {"$type": "string"}},
          "name": "uq_messages_request_role"}),
    ],
    "reports": [
        ([ ("owner_id", 1), ("created_at", -1)], {"name": "ix_reports_owner_created"}),
    ],
    "corrections": [
        ([ ("owner_id", 1), ("document_id", 1), ("created_at", -1)], {"name": "ix_corrections_owner_document_created"}),
    ],
    "audit_events": [
        ([ ("owner_id", 1), ("created_at", -1)], {"name": "ix_audit_owner_created"}),
    ],
    "review_tasks": [
        ([ ("owner_id", 1), ("created_at", -1)], {"name": "ix_reviews_owner_created"}),
        ([ ("assigned_to", 1), ("status", 1), ("updated_at", -1)], {"name": "ix_reviews_assignee_status_updated"}),
        ([ ("owner_id", 1), ("document_id", 1), ("field_id", 1), ("status", 1)], {"name": "ix_reviews_owner_document_field_status"}),
    ],
    "notifications": [
        ([ ("owner_id", 1), ("created_at", -1)], {"name": "ix_notifications_owner_created"}),
    ],
    "user_preferences": [
        ([ ("owner_id", 1)], {"unique": True, "name": "uq_preferences_owner"}),
    ],
    "consents": [
        ([ ("owner_id", 1), ("consent_type", 1), ("created_at", -1)], {"name": "ix_consents_owner_type_created"}),
    ],
    "processing_jobs": [
        ([ ("owner_id", 1), ("status", 1), ("scheduled_at", 1)], {"name": "ix_jobs_owner_status_scheduled"}),
        ([ ("owner_id", 1), ("idempotency_key", 1)],
         {"unique": True, "partialFilterExpression": {"idempotency_key": {"$type": "string"}},
          "name": "uq_jobs_owner_idempotency"}),
    ],
}


def create_indexes(db):
    """Create indexes idempotently. Any index failure is allowed to fail startup."""
    for collection_name, definitions in INDEXES.items():
        collection = db[collection_name]
        for keys, options in definitions:
            collection.create_index(keys, **options)


def connect(uri, database_name, timeout_ms=5000, client_factory=MongoClient):
    client = client_factory(uri, serverSelectionTimeoutMS=timeout_ms)
    try:
        client.admin.command("ping")
        db = client[database_name]
        create_indexes(db)
    except Exception:
        client.close()
        raise
    return client, db
