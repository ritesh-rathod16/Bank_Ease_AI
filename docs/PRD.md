# Product requirements

## Goal

Help customers understand banking and insurance documents, verify extracted figures against page-level evidence, reconcile statement activity, and compare supported loan offers with transparent assumptions. The product must show uncertainty and avoid presenting document analysis as legal or financial advice.

## Current release

The current prototype accepts supported PDFs and bank-statement files up to configured limits. It extracts selected loan terms and clauses, a limited set of explicitly labeled insurance policy fields and clause passages, labeled name/date/address fields from KYC forms, selected financial-statement totals with a same-page balance-sheet equation check, quoted audit-report sections, and supported statement transactions; it provides page-cited extractive Q&A, reducing-balance EMI calculations, loan or statement comparison, and reports. Values use deterministic rules and should be checked against the source document. Audit passages are not interpreted as proof of wrongdoing. KYC extraction does not verify identity or authenticity. Financial-statement extraction does not compare periods, convert units, or interpret accounting policy. Insurance extraction does not verify active coverage or claim eligibility.

## Intended user workflow

1. Create and verify an account, then sign in.
2. Upload an agreement or offer and see processing status.
3. Review extracted terms, evidence, uncertainty, missing values, and conflicts.
4. Correct values with an audit trail.
5. Compare multiple offers and inspect costs, assumptions, and source passages.
6. Save the comparison, export a report, and delete personal data when requested.

## Product requirements

- Private account data must be owned by a user and checked on every read, edit, download, and delete.
- Every extracted fact must retain its source page and supporting text; absent, ambiguous, conflicting, and failed values must remain distinguishable.
- Loan arithmetic must be deterministic, independently tested, and clearly state its assumptions.
- Q&A must retrieve evidence from authorized documents, cite it, and abstain when support is insufficient.
- Users must be able to review and correct extraction without changing a production model automatically.
- Document retention, export, deletion, and consent behavior must be understandable to borrowers.

## Non-goals for the current prototype

The prototype is not an underwriting system, lender recommendation engine, credit decision system, legal opinion, bank integration, or trained proprietary foundation model. It does not claim accuracy on real-world agreements.

## Release gates

Account workflows, MongoDB persistence, authorization isolation, private document storage, financial and extraction benchmarks, deletion behavior, accessibility, and deployment checks must pass before a public launch. Those gates are not currently met.
