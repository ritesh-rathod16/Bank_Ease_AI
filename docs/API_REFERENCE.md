# API reference

FastAPI exposes `/docs` and the JSON endpoints below. Browser traffic reaches the API through the Next.js same-origin rewrites. All document routes require a valid account session; every state-changing request also requires the CSRF cookie/header pair. `/health` is public. Direct API clients must keep the session cookie and send `X-CSRF-Token` on mutations.

## Authentication (`/api/v1/auth`)

| Method and path | Behavior |
|---|---|
| `GET /csrf` | Issues/returns the CSRF cookie used for mutations. |
| `POST /register` | Creates an unverified account; loopback development returns a verification link because no email provider is configured. |
| `POST /verify-email` | Verifies a single-use token. |
| `POST /resend-verification` | Requests another verification link. |
| `POST /login`, `POST /logout` | Starts or revokes the opaque server-managed session. |
| `GET /me`, `PATCH /me` | Read or update the current profile. |
| `POST /forgot-password`, `POST /reset-password` | Request/reset password; development may return a local reset link. |
| `POST /change-password` | Change password and rotate session. |
| `GET /sessions`, `DELETE /sessions/{id}` | List or revoke the current user’s sessions. |
| `GET /export` | Export profile, owned document analyses, normalized statement rows with correction history, saved comparisons, assistant conversations and cited messages, review tasks, and preferences. |
| `GET /export/archive` | Download a private, non-cacheable ZIP containing account data, a manifest, and available original files; the uncompressed archive is capped at 100 MB. |
| `DELETE /me` | Delete the account and local associated records after password + `DELETE` confirmation. |

## Document assistant conversations

| Method and path | Behavior |
|---|---|
| `GET /api/v1/ai/status` | Authenticated provider status: default mode, installed Ollama tags, registered models, configured hosted/search readiness. It does not return provider credentials. |
| `GET /api/v1/conversations?limit=30` | Lists the caller's saved conversation summaries; `limit` is capped at 100. |
| `POST /api/v1/conversations` | CSRF-protected general conversation, or one to five supported owner-owned loan, insurance-policy, financial-statement, audit-report, or statement sources. Accepts legacy `document_id` or `document_ids`; every source is checked independently for ownership, type, readiness, and extracted text/rows. KYC forms are excluded. |
| `GET /api/v1/conversations/{id}/messages?offset=0&limit=100` | Returns owner-scoped questions, answers, and page, extracted-DOCX-section, statement-row, or calculation evidence with source document ID and filename; `limit` is capped at 200. |
| `POST /api/v1/conversations/{id}/messages` | CSRF-protected question using the configured local-first provider and retrieved authorized evidence. Document citations identify a source page or statement row; balance reconciliation uses deterministic imported-row calculations. Falls back to extractive evidence when model inference is unavailable and abstains when evidence does not match. |
| `POST /api/v1/conversations/{id}/messages/stream` | Same request body and access rules as the non-streaming route; returns server-sent `delta`, `done`, and `error` events. Provider errors do not persist an incomplete assistant answer. |
| `DELETE /api/v1/conversations/{id}` | Deletes the caller's conversation and its messages after UI confirmation. Document/account deletion also removes related conversations. |

Message options include `mode` (`local`, `hosted`, `auto`, or `hybrid`), `model`, `allow_hosted`, `allow_hosted_documents`, and `web_research`. Hosted inference is disabled by default. Sending document excerpts to a hosted model requires both per-message hosted permission and separate document-sharing consent. Conversation messages and cited evidence are included in the owner's account export.

## Review queue and staff roles

Roles default to `customer`. A trusted operator provisions the initial `system_admin` by setting `BANKEASE_ADMIN_EMAILS` on the API server to a comma-separated list of verified account emails. Bootstrap administrator roles cannot be changed through the API. Configured administrators may assign non-admin roles to verified accounts; role changes are audited. Branch, loan, employee, auditor, and document-reviewer roles do not grant broad document access.

| Method and path | Behavior |
|---|---|
| `POST /api/v1/reviews` | Owner requests review of one extracted loan field or a statement transaction using `field_id: "transaction:<row_number>"`; body includes `document_id` and optional reason. Statement snapshots preserve imported cells/evidence, effective values, correction revision, and correction reason. An open task for the same field/row is returned rather than duplicated. |
| `GET /api/v1/reviews?document_id=...` | Customers receive their own tasks; staff receive tasks assigned to their account; system admins receive task summaries. Only the assigned staff member receives the field snapshot and cited evidence. |
| `POST /api/v1/reviews/{id}/assign` | Configured administrator assigns/reassigns to a verified staff account by email. |
| `POST /api/v1/reviews/{id}/decision` | Assigned staff records `approved`, `needs_owner_action`, or `rejected`, with an optional comment. |
| `POST /api/v1/reviews/{id}/response` | The owner responds to a `needs_owner_action` task; the configured administrator can then reassign it. |
| `GET /api/v1/admin/users` | Configured administrator lists account identities and roles for controlled provisioning. |
| `PATCH /api/v1/admin/users/role` | Configured administrator sets a non-admin role using `{ "email": "...", "role": "document_reviewer" }`. |
| `GET /api/v1/audit-events` | Configured administrator reads paginated review and role-change events. |

Review snapshots contain the selected loan field's extracted value and cited passage, or the selected statement row's imported/effective values, source cells and evidence. They are removed with the source document or customer account. Staff access is task assignment only; no branch-wide or unrestricted manager permission exists. Review decisions record review status and comments; they do not alter the customer's saved row correction or source transaction. There is currently no general document correction or customer record browsing by reviewer roles.

## Documents and analysis

| Method and path | Behavior |
|---|---|
| `GET /api/v1/dashboard/summary` | Owner-scoped recent documents, completed-analysis count, open review and extraction signals (including audit reports awaiting human review), financial arithmetic discrepancies, clause findings by category, latest home-loan document ID, and future loan dates or insurance policy expiry dates confirmed by the owner. Each obligation includes document/page evidence; recurring dates are not inferred. |
| `GET /api/v1/search?q=...&limit=20` | Searches only the current user's filenames, extracted fields/evidence, clauses, page lines, and statement transaction values; query length is 2-120 and results are capped at 50. |

| Method and path | Behavior |
|---|---|
| `GET /health` | Process status, deterministic fallback mode, active persistence backend, and whether the document worker thread is running. |
| `GET /documents` | Current user’s document summaries. |
| `POST /api/v1/documents` | Multipart `file`; accepts PDFs, PNG/JPEG/TIFF scans, DOCX text documents, statement CSVs, and statement XLSX workbooks up to `MAX_UPLOAD_MB`. It validates signatures/containers, stores the original privately, and returns `202` with a processing ID by default. A durable job performs PDF/image OCR, bounded DOCX text extraction, or statement extraction. DOCX questions cite extracted text sections because Word page layout is not preserved; a rule-based document-type hint may be returned separately, but DOCX remains `unknown` and structured banking fields are not inferred. XLSX formulas are not evaluated; macros and external workbook links are rejected. `BANKEASE_ASYNC_DOCUMENTS=false` selects synchronous processing; test mode is synchronous unless explicitly overridden. |
| `GET /documents/{id}` | Owned document metadata and extracted terms/clauses, excluding page text. |
| `GET /api/v1/documents/{id}/versions` | Owner-scoped version family with generated document IDs, filenames, type, and version sequence; source paths and extracted content are excluded. |
| `GET /api/v1/documents/{id}/preview` | Returns the original PDF or raster image inline, or downloads CSV/XLSX as an attachment, after checking document ownership; response is private and non-cacheable. |
| `GET /api/v1/documents/{id}/transactions?offset=0&limit=100&search=&date_from=&date_to=` | Owner-scoped, paginated statement rows plus imported reconciliation summary; supports case-insensitive search over source and effective values and inclusive ISO date bounds over effective dates; limit is capped at 500. |
| `PATCH /api/v1/documents/{id}/transactions/{row_number}` | Owner-scoped correction body accepts `date`, `description`, `amount_minor`, and `balance_minor`, plus required `expected_revision` and optional `reason`. Omitted fields stay unchanged; `null` reverts a field to its imported value. Original normalized fields, source cell values, and evidence remain unchanged. |
| `GET /api/v1/documents/{id}/fields` | Returns extracted values, original evidence, effective value, and any saved correction history. Supported loan PDFs may include `first_emi_date`, `loan_maturity_date`, and `interest_reset_date`; insurance policies may include labeled policy dates, identity/coverage fields, and money fields; explicitly titled KYC forms may include labeled `full_name`, `date_of_birth`, and `address`; supported financial statements may include labeled totals; audit reports may include explicitly labeled `auditor_name`, `reporting_period`, `report_date`, and `audit_opinion`. Candidates retain source page and passage. Government identity numbers and standalone identity cards are not extracted; KYC extraction does not verify identity. |
| `GET /api/v1/documents/{id}` | Returns owner-scoped document analysis without raw page text. Financial statements include `financial_checks`: a same-page balance-sheet equation result with values and source evidence. Unclear inputs are `not_evaluated`; this is not an accounting opinion. |
| `PATCH /api/v1/documents/{id}/fields/{field_id}` | Owner-scoped correction body `{ "value": "...", "reason": "..." }`; send `value: null` to revert. Source value, evidence, and page are preserved. Date fields require `YYYY-MM-DD`; a saved date correction is the owner's confirmation action. |
| `GET /documents/{id}/status` | Owner-scoped document status plus safe job state, attempt counts, next schedule time, and a bounded error message. |
| `POST /api/v1/documents/{id}/retry` | CSRF-protected, owner-scoped retry of a failed job; returns `202` while the saved source is queued again. |
| `GET /documents/{id}/terms` | Extracted raw/normalized terms, evidence, page and review status. |
| `GET /documents/{id}/clauses` | Rule-detected passages with page and attention text. |
| `POST /documents/{id}/ask` | JSON `{ "question": "..." }`; extractive answer, found flag and page citations for supported non-KYC PDF types. Financial statements are supported. KYC and bank-statement Q&A are unavailable. |
| `POST /documents/{id}/calculate-emi` | Optional principal, annual rate and tenure; missing values use extracted terms. Returns assumptions and repayment summary. |
| `POST /documents/compare`, `POST /api/v1/comparisons` | Two owned IDs and optional comparison-only overrides; returns and saves evidence comparison rows. |
| `POST /documents/compare/report` | Same request; returns a PDF. |
| `GET /api/v1/comparisons?kind=loan&offset=0&limit=20` | Lists the caller’s saved comparison summaries; `kind` is optional and `limit` is capped at 100. |
| `GET /api/v1/comparisons/{comparison_id}` | Reopens a saved result snapshot after checking owner access to the comparison and both source documents. |
| `GET /api/v1/comparisons/{comparison_id}/report` | Builds a PDF from the stored comparison and evidence snapshot without rerunning extraction. |
| `DELETE /api/v1/comparisons/{comparison_id}` | Deletes the caller’s saved comparison. |
| `POST /api/v1/comparisons/statements` | Two owned statement IDs; compares exact transaction multisets, returns source evidence, and saves the result. Amount comparison abstains unless both statements have the same identified currency. |
| `POST /api/v1/comparisons/statements/report` | Same statement IDs; returns a PDF with summary and up to 100 review differences. |
| `POST /api/v1/comparisons/financial-statements` | Two owned financial-statement IDs; returns labeled totals with source evidence and period labels when explicit. Calculates deltas only when both cited rows identify the same currency and scale; saves an owner-scoped snapshot. |
| `GET /documents/{id}/report` | Printable HTML report for an owned document. |
| `DELETE /documents/{id}` | Deletes the owned analysis, page data, comparisons, and original local source; returns 204. |

Comparison overrides are limited to `loan_amount`, `interest_rate`, `tenure`, `emi`, and `processing_fee`. They affect a comparison/report only; they do not alter the original extraction. API schemas may change before a versioned public API is introduced.

Statement CSV import accepts UTF-8 CSV with date, description, and either debit/credit columns or amount plus explicit direction. Selectable-text PDF statements are supported when PyMuPDF detects a table with recognized date, description, and amount columns. Fully scanned PDFs can also produce transaction rows when OCR word coordinates fit a recognized header and column layout. All paths retain reconstructed source values; PDF rows cite page and passage, and OCR rows also carry a source bounding box and `pymupdf_tesseract_ocr` extraction method. OCR-derived rows always have `needs_review` document and statement status and must be checked against the original scan. Mixed selectable-text and scanned pages are not combined automatically; unrecognized headers, ambiguous columns, malformed rows, and unreadable pages remain for review. Missing Tesseract or language data returns a clear service error instead of saving a successful analysis. Balance mismatches and exact duplicate candidates are review signals. Western and Indian comma grouping are supported; values outside the exact JSON integer range are rejected. Slash dates use day/month/year order. PDFs are limited to `MAX_PDF_PAGES` (default 100). This OCR layout path has synthetic-coordinate test coverage only; extraction quality on real bank scans has not been measured.

Scanned loan PDFs and statement pages use PyMuPDF's built-in Tesseract integration. Install Tesseract OCR and language data on the API host; Python package installation alone does not provide the native OCR engine. Default limits are `BANKEASE_OCR_MAX_PAGES=10`, `BANKEASE_OCR_PAGE_TIMEOUT_SECONDS=15`, and `BANKEASE_OCR_DOCUMENT_TIMEOUT_SECONDS=45`. OCR runs in a subprocess with per-page timeouts, image-size limits, a 4 MB page response cap, and a 50,000-word document cap. OCR-derived loan and statement analyses remain `needs_review`; source pages, citations, extraction method, and matching bounding boxes are retained when available. If Tesseract or its language pack is missing, the API reports that instead of treating a scan as analyzed.

Statement comparison matches effective date, signed amount, and whitespace/case-normalized description, using saved owner corrections where present and preserving repeated transactions as separate occurrences. It flags rows seen on only one side and running balances that differ for matched rows. It does not fuzzy-match descriptions, infer fraud, or mutate source rows. The statement's original reconciliation summary remains based on imported data. Saved comparisons retain the request and result snapshot, including both original source evidence and effective values, so opening or downloading a saved result does not silently rerun extraction. Statement snapshots keep at most the first 500 detailed differences while retaining full summary counts. Comparison records are owner scoped, included in account export, and removed when either source document or the account is deleted.

Loan date candidates are extracted only from explicitly labeled first-installment, loan-maturity, and interest-reset dates. ISO and written-month dates are normalized; ambiguous numeric dates and conflicting values receive review status. The dashboard lists only future dates with an owner-saved confirmation, retained source page and passage; it does not infer recurring EMI dates or payment completion.

Loan and statement-row corrections apply only to the selected account-owned document. Supported values are validated; correction records include actor, timestamp, reason, prior/effective values, revision, and action. Loan extraction and statement imported values, source text/cells, page citations, and passages remain unchanged. Saved loan corrections are used by EMI calculations and loan comparisons; saved transaction corrections are used by new statement comparisons and are labeled with their effective values. Statement reconciliation remains a source-data check. Reverting creates another history event rather than deleting correction history.
