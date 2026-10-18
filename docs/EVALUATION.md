# Evaluation (actual executed results)
Command: `python -m pytest backend/tests -q` → **184 passed** (21 original in `test_all.py`, 163 manifest-driven in `test_datasets.py`). Live HTTP check: all 24 sample files returned the manifest's expected status (201 ×20, 422 ×2, 415 ×2); 20 records persisted, 0 raw PDFs on disk.
Covers: file integrity/checksums, page numbers, classification, term values/status/page citations, clause category+page, grounded Q&A and abstention, EMI vs independent values and mismatch, 7 comparison pairs, prompt injection, partial/full scans, malformed/wrong-type/empty uploads, 413 size limit, deletion, retention purge, 0600 permissions, id/path validation, generator reproducibility.
## Defects found by this phase and fixed
1. Possessive `'s` became a token, so "borrower's blood group" matched "Lender's" and returned a wrong answer (qa.py tokenizer).
2. Clause keyword substring matching ("emi" in "premium", "repay" in "prepayment"): now word-boundary prefix matching.
3. Ambiguous/deferred ("to be agreed") and approximate ("Approximately INR…") values were reported as missing/extracted: now `review`.
4. Failed uploads left an orphan record and every upload left a raw PDF on disk: both removed.
## Known limitations (not hidden)
- Q&A is extractive; 2-line chunks can return neighbouring lines. In the prompt-injection sample the injected sentence is never obeyed but may be *quoted* as a second source for fee questions.
- Conflicting values: the first occurrence is reported with status `review`; Q&A cites both pages.
- Tests use templated synthetic text; no accuracy claim for real-world documents. Frontend was not browser-tested in this phase (API was). No latency benchmarks run.
