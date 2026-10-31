# Database schema

## Current storage

By default, the development app stores account/session data in JSON files under `data/runtime/auth`, owner-scoped analysis records in `data/runtime/records`, and accepted original PDF/CSV files under `data/runtime/uploads` with generated document-ID names. Records store only a generated source key, extension, media type, and byte count. The owner-authorized preview route resolves that key only after loading the owner-scoped document. Document deletion, account deletion, and retention purge remove the associated file and owner-scoped derivatives. The document worker performs bounded scheduled retention sweeps; uploads also sweep the current owner's expired documents. Optional MongoDB mode stores the same source metadata and separately indexed page chunks with the owner ID; file bytes remain in private local storage. Mongo indexes are initialized on application startup; a live server connection has not yet been verified. Local records and source files are not encrypted at rest, and JSON is not a production multi-process repository.

Loan-term, policy, KYC, and financial-statement field corrections are stored with each document term as `effective_value`, `correction_revision`, `user_correction`, and append-only `correction_history`. Mongo updates compare the per-field revision and atomically change the effective value plus history; stale writes are rejected, and loan/policy monetary values use Decimal128. Financial-statement arithmetic checks are embedded in the owner-scoped `documents` record and are recomputed atomically with saved field corrections. Statement-row corrections store imported values unchanged alongside `effective`, `correction_revision`, `user_correction`, and append-only `correction_history` in the owner-scoped transaction row. Mongo updates use the expected revision in the row query and atomically save effective values and history; stale writes are rejected. Local JSON uses an owner-checked read/modify/write, atomic file replacement, and a process-local lock. The account export returns normalized statement rows and their correction history in a top-level `transactions` array for both backends. Review tasks store owner/document/field, status, assignment, and a bounded evidence snapshot; statement row snapshots include original source cells/evidence and current effective values. Audit events store action metadata without evidence text. The separate `corrections` collection is indexed for future cross-document queries but is not currently the source of correction history.

## Intended MongoDB collections

MongoDB is the intended production database. User-owned records include a server-generated `owner_id`, timestamps and schema version where implemented. All private document queries include the authenticated owner. Other planned collections below are not necessarily implemented yet.

| Collection | Purpose / key indexes |
|---|---|
| `users` | Normalized email (unique), verification state, password hash, profile and non-admin role; system admins are configured by server allowlist |
| `sessions` | Hashed opaque session token, owner, expiry TTL, revocation and CSRF hash |
| `email_verifications` | Hashed single-use token, owner and expiry TTL |
| `password_resets` | Hashed single-use token, owner and expiry TTL |
| `documents` | Creation-time index for retention sweeps; owner + status + creation-time index; safe metadata and statement summary reference |
| `statements` | Owner + document ID; imported statement reconciliation summary |
| `transactions` | Owner + document ID + row number; immutable imported normalized values, original CSV/PDF/OCR cell evidence, page and OCR bounding box when available, extraction method, effective values and embedded revisioned correction history |
| `document_versions` | Implemented: owner + version-group document + version; generated document ID, previous-version reference, filename, type, and status. Source path remains in the owner-scoped document record. |
| `extracted_fields` | Owner + document + field; raw/normalized value, evidence, page, confidence, review state |
| `document_chunks` | Owner + document + page; text and retrieval metadata |
| `analyses` | Owner + document + timestamp |
| `comparisons` | Implemented: owner + updated time, owner + source document IDs; saved request and immutable bounded result/evidence snapshot. Removed with either source document or owner account. |
| `conversations` | Implemented: owner + one-to-five source document IDs + title + created/updated timestamps + message count; removed when any source document or account is deleted and included in account export |
| `messages` | Implemented: owner + conversation + timestamp; user question or grounded answer and its page citations; removed with conversation/source document/account and included in account export |
| `reports` | Owner + created time; private generated artifact reference |
| `corrections` | Current loan-term history is embedded in `documents`; statement-row effective values and history are embedded in the owner-scoped `transactions` row so source values and correction state update atomically. |
| `review_tasks` | Implemented: owner + created time; assigned reviewer + status + update time; selected evidence snapshot is exposed to owner and assigned reviewer only |
| `audit_events` | Implemented for review and role changes: owner + event time; action metadata without raw document text |
| `notifications`, `user_preferences`, `consents` | Owner + relevant status/time indexes |
| `processing_jobs` | Implemented queue record: owner + document + idempotency key; queued/processing/completed/failed/cancelled status; attempts, scheduled time, worker lease, and bounded error. Uploaded bytes remain in private local storage, not in the queue. |

The initial implementation may combine collections when the access patterns remain clear, but it must preserve ownership, evidence, audit, and deletion capabilities. Monetary persisted values use integer minor units or `Decimal128`, never binary floating point. Large PDFs and reports are stored outside ordinary MongoDB records.

## Index and migration policy

Create indexes during the centralized application startup lifecycle and fail startup if required indexes cannot be established. Use unique normalized email and token hashes; query indexes should begin with `owner_id` where access is owner-scoped. TTL indexes are for expiry cleanup, not authorization: every token/session query still checks expiry and revocation. Schema/index changes must be versioned and documented; migrations must be repeatable and observable.

## Deletion

Account and document deletion must remove or schedule removal of associated source files, versions, extracted text, chunks, embeddings, reports, conversations, corrections, queued jobs, and derived artifacts. Backup expiry may delay permanent erasure and must be disclosed accurately.
