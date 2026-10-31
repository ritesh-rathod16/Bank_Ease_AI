# Accuracy Architecture Assessment

Assessment date: 2026-10-30

## Existing architecture

BankEase is an existing Next.js App Router application backed by FastAPI. The browser uses the typed API client in `frontend/src/lib/api/client.ts`; Next.js rewrites API requests to FastAPI, so session cookies remain same-origin. FastAPI registers authentication, conversation, and review routers and keeps the document, search, comparison, report, and upload routes in `backend/app/main.py`.

The upload path validates the file signature/container and size, stores the original under a generated private filename, and queues an owner-scoped processing job by default. The worker in `backend/app/jobs.py` reads the original only after checking ownership, calls `backend/app/document_processing.py`, and saves the result. Test mode can process synchronously. The existing queue uses MongoDB leases when MongoDB is configured and a locked local JSON repository otherwise; there is no Redis/Celery dependency.

Persistence defaults to local JSON under `data/runtime`; optional MongoDB indexes are declared in `backend/app/database.py`. Documents and extracted terms are stored together in the `documents` collection, page text is stored separately in `document_chunks`, and statement rows and reconciliation summaries use `transactions` and `statements`. Other collections cover accounts, sessions, jobs, comparisons, conversations, review tasks, corrections, and audit events. The Mongo mode has not been verified against a live deployment.

PDF processing first reads selectable text through PyMuPDF. Pages with little text and embedded images can be OCRed through a bounded subprocess that calls PyMuPDF's Tesseract integration. OCR output currently preserves page text and line/word geometry when available. It does not currently preserve Tesseract confidence, apply page-quality-selected preprocessing, or run orientation correction, deskew, and denoising. OCR output and native text share the same `pages` slot, with `ocr_pages` identifying OCR-derived pages.

The seven current capability groups are implemented across these modules:

| Capability | Existing implementation |
|---|---|
| Loan documents and clauses | `backend/app/extract.py`; deterministic EMI and comparison logic in `backend/app/financial.py` and `backend/app/compare.py` |
| Bank statements | `backend/app/statements.py`, `backend/app/spreadsheet_import.py`, and `backend/app/statement_compare.py` |
| Insurance policies | `backend/app/insurance.py` |
| Financial statements | `backend/app/financial_statements.py` |
| Audit reports | `backend/app/audit_reports.py` |
| KYC forms | `backend/app/kyc.py` |
| DOCX and unclassified documents | `backend/app/docx_import.py`; text sections and unverified type suggestions are handled in `backend/app/document_processing.py` |

All categories are selected in `backend/app/document_processing.py`; unknown document types remain unclassified. KYC extraction is limited to labeled name, date of birth, and address fields and excludes government ID numbers.

The application has two Q&A paths: an extractive BM25-style path in `backend/app/qa.py` and the configurable local-first assistant in `backend/app/ai_engine.py` and `backend/app/conversations.py`. Citations are tied to retrieved source identifiers and locations; model citation-label checks do not prove semantic entailment. The assistant is not the source of truth for arithmetic.

Authentication uses opaque cookie sessions, CSRF checks on mutations, owner-scoped document queries, and role-limited assigned review tasks. Corrections retain imported values and evidence and append correction history. Staff review decisions do not directly change extracted values. These are useful existing controls to preserve.

## Working modules and current test baseline

The checked-in tests cover authentication, document upload and formats, job retries/leases, statement parsing and reconciliation, statement corrections, review workflows, data retention, AI routing, and training-data preparation. Before this assessment, the latest recorded full backend run was 320 passing tests. The suite uses mocked OCR results for scanned-document behavior; it is not a real-engine OCR accuracy benchmark.

## Accuracy and production risks

1. Category extractors return similar but not identical dictionaries. There is no common field contract for stable field ID, normalized value, evidence reference, processing run, or schema version.
2. Most term extractors attach a page and source passage, but the application does not uniformly verify that the page exists and the cited passage is present before exposing the field. OCR bounding boxes are attached only when a text-line match succeeds; general bbox validation is absent.
3. OCR confidence is not captured. OCR-derived documents are generally sent to review, but there is no calibrated field-confidence model. No numeric confidence should be presented as a probability until measured against reviewed data.
4. OCR preprocessing is limited to PyMuPDF/Tesseract. Real Tesseract availability, recognition quality, latency, rotated scans, low-resolution scans, and page-level preprocessing policies have not been benchmarked.
5. Some financial fields are normalized through Python `float` in loan and insurance extractors. Statement transaction arithmetic uses integer minor units, and financial-statement normalization uses decimal strings/`Decimal`. Loan and insurance monetary extraction should move to decimal-safe values in a separate compatibility-reviewed step.
6. The local JSON backend is single-process development persistence. Production startup is deliberately blocked; email delivery, encrypted/managed private file storage, host ACLs, distributed rate limits, operational controls, and live Mongo verification remain open.
7. No manually verified, seven-category benchmark currently supports an accuracy claim. Existing synthetic training data is not product-approved.

## Duplicate or overlapping responsibilities

- `backend/app/qa.py` provides extractive per-document answers while `backend/app/ai_engine.py` and `backend/app/conversations.py` provide model-backed conversations. Both are valid paths, but their evidence validation guarantees differ and should converge on a shared evidence contract.
- Owner corrections in `backend/app/store.py` and assigned staff review tasks in `backend/app/reviews.py` are complementary workflows, but there is no unified field decision state connecting a staff decision to owner correction/revalidation.
- `backend/app/main.py` contains a large set of document, comparison, and reporting routes while auth, reviews, and conversations are routers. A future route split can improve maintainability, but moving routes is not required for the initial accuracy fix and risks unnecessary API churn.
- `documents.terms` stores extracted fields while `database.py` also declares an `extracted_fields` index. Current write/read paths use nested terms, so a migration to a separate field collection should not be assumed or performed without a designed migration.

## Safest implementation path

1. Add a common, additive field schema and evidence verifier at the existing boundary after a category extractor returns fields and before the analysis record is persisted. Keep legacy term keys (`value`, `raw`, `page`, `evidence`, `status`) and endpoints unchanged.
2. Verify page bounds, exact normalized source-text presence, raw-value presence, and any supplied bounding box. Missing or invalid evidence must lower the field to review; it must never create a verified status. Keep OCR confidence and extraction confidence explicitly unavailable until the engine and benchmark can supply calibrated values.
3. Extend deterministic validation using existing calculation modules, preserving source values and corrections. Use Decimal for financial arithmetic, and do not auto-correct discrepancies.
4. Improve OCR only after capturing page quality and adding a manually checked benchmark with the real Tesseract engine. Preserve original page text and only reprocess selected pages.
5. Evolve Mongo persistence additively. The initial common field metadata can remain nested with current terms and use the existing document schema; no migration or new collection is required for that step.
6. Bind the existing review UI to evidence and field validation states, then make Q&A consume verified evidence and abstain when citations fail checks.
7. Run regression tests and measure exact-match, evidence-page, bbox, reconciliation, and false-verification rates before claiming accuracy improvements.

## Files for the first implementation step

- Modify `backend/app/document_processing.py` to enrich extracted fields after extraction.
- Add `backend/app/extraction_schema.py` for the shared Pydantic field contract.
- Add `backend/app/evidence.py` for deterministic source and geometry verification.
- Add focused tests under `backend/tests/` for schema compatibility, source matching, page bounds, and invalid boxes.
- Preserve `backend/app/main.py`, `backend/app/store.py`, existing extractor interfaces, and existing frontend types/API response shape; new field metadata is additive.

## First incremental implementation

The existing flow now has an evidence-check stage between category extraction and record persistence:

```text
PDF text/OCR -> existing category extractor -> common field metadata -> page/source/bbox checks -> existing store -> existing API -> review indicators
```

`backend/app/extraction_schema.py` defines the versioned Pydantic field contract. `backend/app/evidence.py` validates page references, exact normalized source passages, raw-value presence, and bounding-box shape and page bounds. It adds deterministic evidence references only when a source passage is actually found. An extracted value whose citation cannot be located is downgraded to review. Native-text fields are called verified only when the cited passage and raw value match; OCR-derived fields stay pending human review even when their text is located. Confidence scores remain null and explicitly uncalibrated.

The same module attaches citation state to clauses/report passages and transaction rows. Statement CSV/XLSX rows identify their original source cells and row locator; PDF rows carry page-citation checks. The existing API fields (`value`, `raw`, `page`, `evidence`, and `status`) remain present, and the new identifiers, normalized decimal string, evidence state, and processing-run/schema versions are additive. Metadata remains nested with the current document/transaction records, so this increment requires no MongoDB migration or collection change.

The loan EMI formula, totals, and stated-versus-calculated difference now use `Decimal` internally with explicit half-up cent rounding. Existing routes still return JSON numbers. The frontend displays whether source text matched, whether a passage was located but still needs OCR review, or whether the citation needs review.

Files changed or added by this increment: `backend/app/document_processing.py`, `backend/app/financial.py`, `backend/app/evidence.py`, `backend/app/extraction_schema.py`, `backend/tests/test_all.py`, `backend/tests/test_evidence.py`, `backend/tests/test_statement_pdf.py`, `frontend/src/types/api.ts`, `frontend/src/features/documents/document-detail.tsx`, and `frontend/src/features/documents/statement-transaction-row.tsx`.

Verification for this increment: all 331 backend tests pass; frontend ESLint, TypeScript checking, and the optimized production build pass; Python byte-compilation and `git diff --check` pass.

There is no accuracy claim from this change: exact source matching checks traceability, not whether a category extractor chose the correct governing value. The remaining OCR calibration, real-engine benchmark, term-level statement field contract, and broader reviewer approval workflow are still open.

## Not part of the first step

There is no live MongoDB migration, real-engine OCR claim, production-readiness certification, model-confidence calibration, or seven-category accuracy benchmark in this phase. Those require deployment access, a human-reviewed corpus, or external infrastructure not present in this repository.
