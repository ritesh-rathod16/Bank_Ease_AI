# BankEase AI
Document-grounded loan analysis. Zero cost, no API key, no GPU: **deterministic rule-based extraction + BM25 retrieval + exact EMI maths.** Answers are extractive quotes with page citations; the app never invents values (missing = `null`, uncertain = `review`).

## Quick start (one command runs backend + frontend)
```bash
make install      # once: install dependencies (or: npm run install:py)
make dev          # preflight checks, then backend + frontend together on http://localhost:8000
# equivalents: npm run dev:all   |   python3 scripts/dev.py [--open] [--no-reload]
```
The frontend is a static page served by the FastAPI backend, so one process and one port serve both (UI at `/`, API docs at `/docs`). No `npm install` is needed; `package.json` only provides script aliases.

| Command | What it does |
|---|---|
| `make check` | Preflight: Python 3.10+, all dependencies, app imports, frontend file, dataset checksums, writable storage, port free |
| `make dev` / `make start` | Preflight then run with / without auto-reload (`PORT=9000 make dev` to change port) |
| `make test` | Preflight then the full suite (184 tests) |
| `make data` | Regenerate synthetic dataset and manifests |
| `make clean` | Remove caches and local runtime records |
| `make venv` | Create `.venv` (then `. .venv/bin/activate && make install`) |

Windows: `make` is not built in; use `python scripts/dev.py` or `make PYTHON=python ...` (the npm scripts call `python3`; edit `package.json` if your command is `python`).
Env vars: `PORT` (8000), `MAX_UPLOAD_MB` (10), `BANKEASE_RETENTION_HOURS` (24; `0` disables purge), `BANKEASE_DATA` (storage dir, default `data/runtime`), `CORS_ORIGINS`.

## Synthetic datasets (`data/samples/`, all fictional; see `docs/DATASET.md`)
| Folder | Files | Purpose |
|---|---|---|
| `personal/` | 4 | fixed/floating, flat fee, **EMI mismatch**, **missing fee** |
| `home/` | 2 | floating (benchmark-linked), fixed with foreclosure charge, mortgage + insurance |
| `vehicle/` | 2 | down payment, hypothecation, default, repossession |
| `sanction_schedule/` | 3 | sanction letters (personal, home), 36-row repayment schedule |
| `comparison_edge/` | 13 | baseline vs rate/fee changes, identical copy, missing fields, conflicting values, ambiguous terms, prompt injection, partially scanned, fully scanned (rejected), malformed, wrong type, empty |
Machine-readable: `data/samples/manifest.json` (expected terms/pages/clauses/Q&A/EMI/comparisons + sha256) and `manifest.csv`.

## Manual upload test
Start the server, open http://localhost:8000, choose a file from the table above, click **Analyze**. Expected: terms table with status tags and evidence, EMI check, attention points, and "Ask your loan" citing pages. Rejected files (`scanned_loan_agreement.pdf` 422, `malformed_loan_agreement.pdf` 422, `not_a_pdf.pdf` / `empty_file.pdf` 415) must show a clear error. Same via API:
`curl -F file=@data/samples/home/home_loan_agreement_01.pdf localhost:8000/documents/analyze`

## Storage and deletion (sensitive documents)
- Raw uploaded PDFs are parsed in memory and **never written to disk**; failed uploads leave no record.
- Per-document analysis record (full extracted text) is a JSON file in `data/runtime/records/` with mode 0600 (dir 0700). **Not encrypted at rest.**
- `DELETE /documents/{id}` removes it; records older than `BANKEASE_RETENTION_HOURS` are purged on each upload.

## Status
Implemented: everything above plus classification, evidence-backed term extraction, clause categories with neutral attention points, comparison, HTML report, "needs review" for ambiguous/qualified/conflicting values.
**Not implemented:** OCR (scanned pages are reported as unreadable; image-only PDFs rejected), LLM adapters, embeddings/FAISS, MongoDB (local JSON only), PDF export, DOCX, UI for comparison, accounts, fine-tuning (deliberately postponed), real public datasets (none downloaded).
Informational use only; not legal or financial advice.
