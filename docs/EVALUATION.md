# Evaluation (actual executed results)

## DOCX, scanned image, XLSX, and statement-chat verification (2026-10-20)

- Added bounded OpenXML DOCX text extraction, private original storage/download, direct and conversation Q&A with extracted-section citations (never fabricated page numbers), and a test for owner-scoped document ingestion and retrieval. Structured banking fields and Word page layout are not extracted.
- Added model-registry verification: an installed Ollama fine-tuned conversation model is selectable; an experimental classification adapter is not, and unavailable models are not shown as selectable.
- DOCX upload/search/assistant and model-registry tests passed; the full backend regression suite: **314 passed** using `data/runtime/.venv-review/Scripts/python.exe -m pytest backend/tests -q --basetemp=data/runtime/.pytest-ai-registry-final -p no:cacheprovider --tb=short`.
- `npm.cmd run lint`, `npm.cmd run typecheck`, and `npm.cmd run build` passed. Windows Application Control blocks the installed native SWC binary, so the successful build used a writable workspace `LOCALAPPDATA` cache and Next's WASM fallback.
- The production server returned HTTP 200 for `/dashboard`, `/dashboard/upload`, `/dashboard/documents`, and `/login`; a generated JavaScript asset returned 200 (29,981 bytes) and the Tailwind CSS asset returned 200 (46,111 bytes). Interactive visual browser checks remain unavailable because browser control did not initialize in this environment.
- Image OCR used a controlled OCR fixture; real Tesseract accuracy/latency is unverified. XLSX and DOCX parsing used synthetic OpenXML fixtures; there is no issuer-document benchmark. Legacy XLS remains unsupported.

## Universal AI engine verification (2026-10-20)

- The authenticated assistant supports general chat, selected-document retrieval, statement-row evidence, SSE responses, local/optional-hosted routing, and optional web search.
- Isolated frontend HTTP smoke returned 200 for `/dashboard` and its CSS; unauthenticated `/api/v1/ai/status` returned 401.
- No Ollama binary/model, hosted credential, or SearXNG endpoint was available. Live model inference, hosted routing, and web search remain unverified; no model or dataset was downloaded and no new conversational fine-tuning was run. The in-app browser failed to initialize (`missing field sandboxPolicy`), so authenticated visual interaction remains unverified.


Latest financial-statement comparison and dashboard CSS verification (2026-10-20): `data/runtime/.venv-review/Scripts/python.exe -m pytest backend/tests -q --basetemp=data/runtime/pytest-financial-period-final -p no:cacheprovider` -> **294 passed**. Coverage includes explicit reporting-period labels, matching-currency-and-scale deltas, unit-mismatch abstention, saved snapshot/PDF, and cross-owner access denial. `npm.cmd run lint`, `npm.cmd run typecheck`, `npm.cmd run build`, Python compilation, and `git diff --check` passed. `/dashboard`, `/dashboard/compare`, and the current CSS chunk returned HTTP 200; OpenAPI includes the financial-statement comparison route. No browser executable or initialized in-app browser was available, so visual inspection was not run.
Historical prototype baseline before the later workflows: `python -m pytest backend/tests -q` → **184 passed**. A live HTTP check at that point covered 24 sample files. That baseline preceded original-file retention.

Earlier backend verification after private preview, statement filters, version lineage, and archive export: `python -m pytest backend/tests -q --basetemp=data/runtime/.pytest-bankease-export -p no:cacheprovider` → **244 passed** (Python 3.14.2). Frontend typecheck, lint, production build, and preflight passed. The Next.js production server successfully served the UI and proxied CSRF, registration, verification, login, profile, and document-list requests to a local API instance. MongoDB behavior is covered with test doubles only. No live MongoDB server or Atlas connection has been run.
Covers: file integrity/checksums, page numbers, classification, term values/status/page citations, clause category+page, grounded Q&A and abstention, EMI vs independent values and mismatch, 7 comparison pairs, prompt injection, partial/full scans, malformed/wrong-type/empty uploads, 413 size limit, generated-name private PDF storage and owner-authorized preview, CSV import, deletion, retention purge, POSIX 0600 permissions, id/path validation, and generator reproducibility.
## Defects found by this phase and fixed
1. Possessive `'s` became a token, so "borrower's blood group" matched "Lender's" and returned a wrong answer (qa.py tokenizer).
2. Clause keyword substring matching ("emi" in "premium", "repay" in "prepayment"): now word-boundary prefix matching.
3. Ambiguous/deferred ("to be agreed") and approximate ("Approximately INR…") values were reported as missing/extracted: now `review`.
4. Failed uploads could leave an orphan record; persistence now happens only after validation and parsed results are saved with rollback cleanup.
## Known limitations (not hidden)
- Q&A is extractive; 2-line chunks can return neighbouring lines. In the prompt-injection sample the injected sentence is never obeyed but may be *quoted* as a second source for fee questions.
- Conflicting values: the first occurrence is reported with status `review`; Q&A cites both pages.
- Tests use templated synthetic text; no accuracy claim for real-world documents. The frontend was checked through its production build and same-origin HTTP flow; no interactive browser automation or latency benchmarks were run.

## Statement ingestion workflow (2026-10-19)
- Added a synthetic four-row statement fixture with an exact duplicate candidate and a deliberately inconsistent running balance.
- Added CSV and PDF-table parser coverage, normalization, ambiguous-input rejection, PDF evidence pages, unsupported-layout review, owner-scoped upload/read/pagination/report, deletion cleanup, and Mongo transaction-storage checks.
- Added a PDF page-count guard and regression test; the default maximum is 100 pages.
- Full backend suite at ingestion: `212 passed` on Python 3.14.2. The current statement-focused selection passes 26 tests. Frontend typecheck, lint, and production build passed; preflight passed.
- Synthetic statement tests cover CSV, a generated ruled-table PDF, and OCR word-coordinate fixtures for full scanned pages, including row evidence, bounding boxes, reconciliation, and abstention on malformed layouts. They do not measure extraction accuracy on bank-produced PDFs. The API host in this environment has no Tesseract executable, so no real-engine accuracy or latency benchmark was run; OCR-derived statement records remain marked for human review.

## Statement comparison workflow
- Comparisons use exact date, signed amount, and normalized description keys, preserve duplicate occurrences, cite both source rows, and abstain when currencies are missing or differ.
- Tests cover transaction differences, running-balance changes, currency mismatch, report generation, and owner isolation. Results are deterministic review signals, not fraud findings.

## Owner-scoped term correction workflow
- Tests cover normalized corrections, unchanged source value/page/evidence, actor/reason/timestamp history, revert-with-history behavior, owner isolation, loan-statement rejection, EMI/comparison use, and Decimal128 persistence with Mongo test doubles.
- Frontend presents saved correction values beside the original source passage and page, supports editing and reverting, and exposes correction history.
- Corrections are attached to customer-owned loan terms; there is not yet a manager assignment/review queue or a general audit-event service.

## Saved comparison history workflow (2026-10-20)
- Loan and statement comparisons are persisted per owner with the submitted request, result summary, evidence-bearing result snapshot, and source document IDs. Lists are paginated and omit detailed snapshots until reopened.
- Tests cover saved snapshot reopen, saved PDF generation without re-extraction, owner isolation, deletion, source-document cascade, account cascade in Mongo test doubles, and inclusion in personal data export.
- Comparison history uses owner-scoped pagination and validates type and page-size filters.
- Statement snapshots store up to 500 detail differences while retaining full comparison counts; the UI labels truncated history. Saved history uses extracted evidence and is deleted with either source document.
- No live MongoDB server or browser-driven visual interaction has been verified.

## Private source storage and preview
- Accepted PDF/CSV files use generated document-ID filenames under private `data/runtime/uploads`; the frontend receives only an owner-checked preview route, never a public file URL.
- Tests verify preview ownership, no-store/nosniff headers, generated path, POSIX file permissions, and source deletion during document deletion and retention purge. Windows ACL behavior and MongoDB-backed source cleanup have not been verified against live services.
- Files are not encrypted at rest. Retention is checked on the owner's next upload, not by a background worker, so inactive accounts may retain files beyond the configured threshold.

## Statement search and date filters
- The transaction endpoint and statement screen now filter owner-scoped rows by case-insensitive source text and inclusive ISO date bounds before pagination.
- Tests cover filtered pagination, date validation, and Mongo-backed repository behavior through its test double. Current filters read the owned statement rows before applying filters; large-scale query latency has not been measured.

## Document version lineage
- Uploads may supersede only the latest version in the same owner-scoped document family and supported type. Version history omits source paths and extracted content.
- The review screen can upload a revision and compare versions through the existing loan or statement comparison engine. Tests cover version numbering, lineage, stale-parent rejection, owner isolation, and source-backed comparison output. MongoDB storage is covered by repository doubles; concurrent live writes have not been exercised against a Mongo server.

## Complete account export
- The authenticated archive endpoint returns account data, a manifest, and available originals under generated archive paths; no stored filesystem path is included. It caps uncompressed output at 100 MB and disables caching.
- Tests inspect ZIP contents, owner isolation, headers, source inclusion, and the size cap. Large real accounts and browser download UX have not been benchmarked.

## Synthetic ML data preparation and deterministic baseline (2026-10-20)

- Added `scripts/prepare_ml_dataset.py`. It verifies synthetic provenance, file containment, PDF signature, manifest checksum, page count, and source marker, then emits deterministic document/field JSONL plus split metadata and evidence spans.
- Prepared 20 accepted synthetic documents and 140 field labels across three source/layout families. A whole-family split yields 17 train agreement PDFs, 2 validation sanction-letter PDFs, and 1 test repayment-schedule PDF. Since the types are confounded with the three layouts, every split lacks class coverage; the manifest correctly reports `training_ready: false`.
- Added `scripts/evaluate_synthetic_baseline.py`, which calls the existing rules without training or changing models. It matched document type and loan category on 20/20 fixture documents. On 19 selectable-text documents (one partial-text fixture excluded), field value and status checks matched 133/133; page checks matched 124/124 applicable labels; evidence support matched 122/122 labels with raw source values.
- These are in-sample synthetic fixture checks with all known templates included. They are regression results only, not held-out, production, or real-world accuracy claims. OCR was not used. The generated corpus and JSON report are runtime artifacts under `data/runtime/ml/`.
- Added coverage for reproducibility, source-family isolation, evidence offsets, checksums, path traversal rejection, and refusal of non-synthetic input.

## Held-out synthetic layout-family check

- Added `scripts/make_layout_variants.py` to produce a second synthetic corpus of 9 PDFs and 63 field labels across three different layouts. Every family contains all three document types, allowing each complete family to be assigned to exactly one split.
- Corrected the group splitter after a new test exposed that its former cost heuristic could assign all three equally sized families to train. The current greedy fill-ratio splitter distributes these families across train, validation, and test, each with all three document types.
- Held-out `layout-form-v1` test family: deterministic classification matched document type and loan category on 3/3 documents. Field value, status, page, and evidence checks each matched 21/21 labels. There is one document per type in test, and all labels are generated, not human-reviewed; treat this only as a splitter and parser regression check, not generalization or production accuracy.
- The preparation manifest reports class coverage but keeps training blocked for unreviewed synthetic labels and the minimum per-class counts (20 train, 5 validation, 10 test).

## Statement transaction corrections (2026-10-20)

- Added an owner-scoped CSRF-protected correction route with strict date/amount validation, required optimistic revision, field-level reversion, and append-only correction history. Original parsed values, source CSV/PDF cells, and page evidence remain unchanged.
- Both local JSON and Mongo transaction-row paths preserve correction state. Mongo writes match the expected row revision and apply effective values plus history in one update. Fake-Mongo regression coverage verifies stale-write rejection and owner-filtered export.
- New statement comparisons use effective corrected date/description/amount/balance while retaining the original source evidence; imported reconciliation summaries remain unchanged. Owner JSON and ZIP exports include normalized statement rows and correction history.
- The statement UI now supports editing, reversion, original-value viewing, and history; TypeScript, ESLint, and the Next.js production build passed.

### Latest verification

- Cross-document assistant and dashboard polish (2026-10-20): `data/runtime/.venv-review/Scripts/python.exe -m pytest backend/tests -q --basetemp=data/runtime/.pytest-bankease-dashboard-final --disable-warnings` -> **286 passed, 1 warning**. The suite includes citations from both selected documents, insufficient-evidence abstention, cross-account access denial, and conversation cleanup when a source is deleted. Frontend lint, TypeScript, production build, Python compilation, and `git diff --check` passed after the latest UI updates. The production Next.js server returned HTTP 200 for dashboard, upload, documents, search, calendar, comparison, and login routes on port 3017; this route-only check did not include the API. The in-app browser connection could not initialize in this session, so visual and authenticated interaction are unverified.
- Scanned statement OCR extraction (2026-10-20): full backend suite **285 passed**; `npm.cmd run lint`, `npm.cmd run typecheck`, `npm.cmd run build`, Python `compileall`, and `git diff --check` passed. Synthetic OCR-word fixtures verify coordinate-based column mapping, exact date/amount parsing, page/bounding-box evidence, balance reconciliation, multi-page repeated headers, omitted unreadable-page reporting, mixed-page refusal, changed-layout refusal, abstention on malformed rows, and output/word caps. The API environment has no Tesseract executable, so these tests exercise parser output with controlled OCR words rather than real recognition; no field accuracy or latency claim is made. OCR transactions remain `needs_review`.
- Evidence-backed loan obligations (2026-10-20): `data/runtime/.venv-review/Scripts/python.exe -m pytest backend/tests -q --basetemp=data/runtime/.pytest-obligations-full --disable-warnings` -> **276 passed**. Coverage includes explicit date extraction with page evidence, ambiguous and conflicting date review, user correction validation, owner confirmation/reversion, future-only dashboard listing, owner isolation, and source evidence for stated EMI amounts. `npm.cmd run lint`, `npm.cmd run typecheck`, `npm.cmd run build`, Python compilation, and `git diff --check` passed. Dashboard and calendar now use one evidence-rendering component. Visual browser inspection was not available because the in-app browser connection could not initialize.
- Scheduled retention cleanup (2026-10-20): `data/runtime/.venv-review/Scripts/python.exe -m pytest backend/tests -q --basetemp=data/runtime/.pytest-retention-verified --disable-warnings` -> **274 passed**. Added coverage verifies FastAPI worker startup/configuration, bounded scheduling, owner-scoped and global cleanup in local JSON mode, cancellation of in-flight processing without document resurrection, source and derived-record deletion, and preservation of fresh records. MongoDB date-query construction and index configuration have test-double coverage only; no live Mongo service was available. Python compilation and `git diff --check` passed.
- Assistant conversation history, dashboard integration, and unsupported-PDF handling (2026-10-20): full backend suite **265 passed** using `.venv-review`; tests cover owner-scoped conversation creation, cited question/answer persistence, export, unauthorized access, deletion, source/account cleanup, and unsupported PDFs not being reported as structured analysis. `npm.cmd run lint`, `npm.cmd run typecheck`, `npm.cmd run build`, Python `compileall`, and `git diff --check` passed. A fresh production server on port 3001 returned 200 for `/dashboard`, `/dashboard/upload`, `/dashboard/documents`, and `/login`; unauthenticated `/api/v1/conversations` returned 401 through the frontend proxy, and API health returned 200 with the local JSON worker running. The existing server on port 3000 returned 404 for the new upload route and was left untouched because its process could not be identified. The in-app browser could not initialize, so visual and authenticated interactive checks remain outstanding.
- Durable document-processing queue verification (2026-10-20): `data/runtime/.venv-review/Scripts/python.exe -m pytest backend/tests -q --basetemp=data/runtime/.pytest-bankease-durable-final --disable-warnings` -> **262 passed**. The suite includes queued uploads, owner-scoped status and retry, worker completion/failure, startup recovery/idempotency, lease expiry, and deletion cleanup. `npm.cmd run lint`, `npm.cmd run typecheck`, `npm.cmd run build`, Python `compileall`, and `git diff --check` passed. The running API returned health 200 with `persistence=local-json` and `document_worker=running`; `/dashboard` returned 200. This verifies the local JSON worker path only; no live Mongo lease/concurrency check or authenticated interactive browser check was run.
- Dashboard replacement verification (2026-10-20): full backend suite **259 passed**; `npm.cmd run lint`, `npm.cmd run typecheck`, and `npm.cmd run build` passed. The production build includes dashboard, library, search, tool, compare, review, settings, and document routes.
- HTTP verification against the running app: `/dashboard`, `/dashboard/library/banking`, `/dashboard/search?q=loan`, `/dashboard/tools/calendar`, and `/dashboard/compare` returned 200. The same-origin dashboard summary API returned 401 without a session, as required. API health returned 200 and reported local JSON persistence plus deterministic fallback. Dashboard data was not inspected in an authenticated interactive browser session.

#### Previous baseline before dashboard changes
- Full backend suite: `data/runtime/.venv-review/Scripts/python.exe -m pytest backend/tests -q --basetemp=data/runtime/.pytest-bankease-full-final-20261020 -p no:cacheprovider` -> **255 passed**.
- Focused statement, comparison, Mongo persistence, and account export tests: **44 passed**.
- Dataset preparation generated both the original 20-PDF/140-label corpus and the 9-PDF/63-label layout corpus; both correctly report `training_ready: false`.
- The held-out evaluator completed on the 3-document test family with the results above. The targeted tests cover deterministic generated PDFs, family isolation, split coverage, held-out metrics, and validation failures.
- `scripts/preflight.py --skip-port` passed. Port 8000 remains occupied by an unidentified existing Python process and was left running. Python compilation/help and `git diff --check` passed.
- `npm.cmd run typecheck`, `npm.cmd run lint`, and `npm.cmd run build` passed after adding the statement correction UI.
- No live MongoDB, OCR engine, or external training dataset has been used. The synthetic LoRA classifier experiment is recorded below and is not a real-document benchmark.

## Synthetic classifier fine-tuning experiment (2026-10-20)

- Generated 201 fictional PDFs across 67 source families; preparation wrote 1,407 field labels. Whole-family partitions contain 141 train, 30 validation, and 30 test documents, with all three classes present in each split and 10 examples per class in validation/test.
- The normal training gate correctly rejected the construction-derived, unreviewed labels. An explicit research-only override was used to run GPT-2 sequence classification with LoRA on CPU for three epochs. The highest validation accuracy was 26.7% at epoch 2.
- The saved best-validation checkpoint scored 14/30 test documents correctly (46.7% accuracy; 43.5% macro F1). Existing deterministic title rules scored 30/30 on the same synthetic test PDFs. The learned adapter is not integrated into the app.
- Reloading the saved adapter from its artifact directory reproduced the recorded test score exactly: 14/30.
- The generator reuses only three base layout styles across partitions, so the test is not an unseen-layout test. This measures synthetic variants from one generator, not bank-issued documents, and provides no real-world model-quality estimate. The adapter, artifact checksums, model card, dependency versions, and detailed class metrics live under ignored `data/runtime/ml/gpt2-classifier-v1/` runtime data.
- The training entry point refuses unreviewed data unless `--allow-synthetic-research` is set; it also checks exact manifest matching, source hashes, family-level split isolation, minimum class counts, and all-split class coverage. Focused gate and metrics tests pass.
