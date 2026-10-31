"""Rule-based classification, field extraction and clause intelligence (no LLM; transparent regexes)."""
import re
from datetime import date, datetime

AMT = r"(?:Rs\.?|INR|₹)\s*[\d,]+(?:\.\d+)?"
NUM = r"[^0-9\n]{0,40}?"
FIELDS = {
    "loan_amount": r"(?:loan amount|sanctioned amount|principal amount|amount of loan)" + NUM + "(" + AMT + ")",
    "interest_rate": r"(?:rate of interest|interest rate|annual interest rate)" + NUM + r"(\d+(?:\.\d+)?)\s*%",
    "tenure": r"tenure" + NUM + r"(\d+\s*(?:months?|years?))",
    "emi": r"(?:equated monthly instal?lment|monthly instal?lment|\bemi\b)" + r"[^0-9\n]{0,50}?(" + AMT + ")",
    "processing_fee": r"processing fee" + NUM + "(" + AMT + r"|\d+(?:\.\d+)?\s*%)",
    "late_fee": r"(?:late payment|penal|overdue)[^0-9\n]{0,40}?(" + AMT + r"|\d+(?:\.\d+)?\s*%)",
}
_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DATE_TEXT = rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}\s+{_MONTH}\.?[,]?\s+\d{{4}}|\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})"
DATE_FIELDS = {
    "first_emi_date": rf"(?:first\s+emi(?:\s+payment)?\s+(?:due\s+)?date|due\s+date\s+of\s+first\s+emi|first\s+(?:installment|instalment)\s+date|emi\s+commencement\s+date|first\s+repayment\s+date)[^0-9\n]{{0,20}}?({_DATE_TEXT})",
    "loan_maturity_date": rf"(?:loan\s+maturity\s+date|maturity\s+date\s+of\s+(?:the\s+)?loan|loan\s+end\s+date|final\s+repayment\s+date)[^0-9\n]{{0,20}}?({_DATE_TEXT})",
    "interest_reset_date": rf"(?:(?:next\s+)?interest\s+(?:rate\s+)?reset\s+date|next\s+rate\s+reset\s+date)[^0-9\n]{{0,20}}?({_DATE_TEXT})",
}
FIELDS.update(DATE_FIELDS)
CATS = {
    "Repayment obligations": ["repay", "instal", "emi", "due date"],
    "Interest and rate changes": ["interest", "floating", "benchmark", "reset"],
    "Fees and charges": ["fee", "charges"],
    "Late payment": ["late payment", "overdue", "penal"],
    "Default": ["default", "recall", "event of default"],
    "Prepayment and foreclosure": ["prepay", "foreclos", "pre-clos"],
    "Collateral and security": ["collateral", "security", "hypothec", "mortgage"],
    "Insurance": ["insur"],
    "Borrower obligations": ["borrower shall", "borrower must", "borrower agrees"],
    "Dates and deadlines": ["within", "notice", "days of", "commence"],
}
EXPLAIN = {
    "Repayment obligations": ("Describes how and when you repay.", "Check due dates and installment amount against your budget."),
    "Interest and rate changes": ("Describes how interest is charged or may change.", "Confirm whether the rate is fixed or can reset."),
    "Fees and charges": ("Lists charges payable in addition to interest.", "Add these costs when comparing offers."),
    "Late payment": ("Describes consequences of paying late.", "Note the charge and any grace period."),
    "Default": ("Describes what the lender may do if obligations are not met.", "Review the triggers and the lender's rights."),
    "Prepayment and foreclosure": ("Describes closing or part-paying the loan early.", "Check whether a prepayment charge applies."),
    "Collateral and security": ("Describes assets pledged against the loan.", "Understand what is pledged and when it is released."),
    "Insurance": ("Refers to insurance connected to the loan.", "Check who pays and whether it is mandatory."),
    "Borrower obligations": ("Lists duties placed on the borrower.", "Confirm you can meet each obligation."),
    "Dates and deadlines": ("Mentions time limits or notice periods.", "Note the deadlines."),
}

PRIORITY = ["Prepayment and foreclosure", "Late payment", "Default"]
ORDERED = sorted(CATS.items(), key=lambda kv: kv[0] not in PRIORITY)

def _kw(low, kws):
    return any(re.search(r"\b" + re.escape(k) + (r"\b" if len(k) <= 3 else ""), low) for k in kws)

def pnum(s):
    m = re.search(r"[\d,]+(?:\.\d+)?", s)
    return float(m.group().replace(",", "")) if m else None

def _line(text, pos):
    a = text.rfind("\n", 0, pos) + 1
    b = text.find("\n", pos)
    return text[a: b if b != -1 else len(text)].strip()

def classify(pages):
    head = " ".join(pages[:1])[:700].lower()
    full = " ".join(pages).lower()
    from . import kyc, insurance
    if kyc.is_kyc_form(pages):
        return "kyc_form", None
    if insurance.is_policy_document(pages):
        return "insurance_policy", None
    statement_title = re.search(
        r"(?im)^\s*(?:bank statement|account statement|statement of account)(?:\b|\s*[-–:])",
        head,
    )
    statement_columns = (
        re.search(r"\b(?:transaction date|txn date|tran date|date)\b", full)
        and re.search(r"\b(?:narration|particulars|description)\b", full)
        and re.search(r"\b(?:debit|withdrawal)\b", full)
        and re.search(r"\b(?:credit|deposit)\b", full)
        and re.search(r"\b(?:balance|closing balance)\b", full)
    )
    if statement_title or statement_columns:
        return "bank_statement", None
    from . import audit_reports, financial_statements
    if audit_reports.is_audit_report(pages):
        return "audit_report", None
    if financial_statements.is_financial_statement(pages):
        return "financial_statement", None
    rules = [("kfs", ["key facts statement"]), ("sanction_letter", ["sanction letter", "pleased to sanction"]),
             ("repayment_schedule", ["repayment schedule", "amortization schedule", "amortisation schedule"]),
             ("fee_schedule", ["schedule of fees", "fee schedule"]), ("loan_agreement", ["loan agreement"]),
             ("annexure", ["annexure"])]
    dtype = "unknown"
    for src in (head, full):
        hit = next((k for k, ws in rules if any(w in src for w in ws)), None)
        if hit:
            dtype = hit
            break
    cats = {"personal": ["personal loan"], "home": ["home loan", "housing loan"], "vehicle": ["vehicle loan", "auto loan", "car loan"], "education": ["education loan", "student loan"]}
    sc = {k: sum(full.count(w) for w in ws) for k, ws in cats.items()}
    best = max(sc, key=sc.get)
    return dtype, (best if sc[best] else None)


INSURANCE_TEXT_FIELDS = {"policy_number", "insurer", "product_name", "policyholder"}
INSURANCE_MONEY_FIELDS = {"sum_insured", "premium_amount"}
INSURANCE_DATE_FIELDS = {"policy_start_date", "policy_expiry_date"}
KYC_TEXT_FIELDS = {"full_name", "address"}
KYC_DATE_FIELDS = {"date_of_birth"}
FINANCIAL_AMOUNT_FIELDS = {"total_assets", "total_liabilities_and_equity", "total_liabilities", "total_equity", "total_revenue", "total_income", "total_expenses", "profit_after_tax"}
AUDIT_TEXT_FIELDS = {"auditor_name", "reporting_period", "report_date", "audit_opinion"}
CORRECTABLE_FIELDS = set(FIELDS) | INSURANCE_TEXT_FIELDS | INSURANCE_MONEY_FIELDS | INSURANCE_DATE_FIELDS | KYC_TEXT_FIELDS | KYC_DATE_FIELDS | FINANCIAL_AMOUNT_FIELDS | AUDIT_TEXT_FIELDS | {"interest_type"}
CORRECTABLE_DATE_FIELDS = set(DATE_FIELDS) | INSURANCE_DATE_FIELDS | KYC_DATE_FIELDS
LOAN_DOCUMENT_TYPES = {"loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure"}

QUAL = re.compile(r"approx|about|up to|estimated|indicative|subject to|not exceeding", re.I)
LABELS = {"loan_amount": r"loan amount|sanctioned amount|principal amount", "interest_rate": r"rate of interest|interest rate",
          "tenure": r"tenure", "emi": r"equated monthly instal?lment|monthly instal?lment|emi", "processing_fee": r"processing fee",
          "first_emi_date": r"first\s+emi(?:\s+payment)?\s+(?:due\s+)?date|due\s+date\s+of\s+first\s+emi|first\s+(?:installment|instalment)\s+date|emi\s+commencement\s+date|first\s+repayment\s+date",
          "loan_maturity_date": r"loan\s+maturity\s+date|maturity\s+date\s+of\s+(?:the\s+)?loan|loan\s+end\s+date|final\s+repayment\s+date",
          "interest_reset_date": r"(?:next\s+)?interest\s+(?:rate\s+)?reset\s+date|next\s+rate\s+reset\s+date"}

def _label(name, pages):
    """A 'Label:' line that exists but yielded no usable value -> needs review (not missing)."""
    rx = LABELS.get(name)
    for i, t in enumerate(pages, 1):
        m = rx and re.search(r"^\s*(?:" + rx + r")\b[^:\n]{0,40}:", t, re.I | re.M)
        if m: return (i, _line(t, m.end()))
    return None

def extract_terms(pages):
    terms = {}
    for name, rx in FIELDS.items():
        hits = []
        for i, t in enumerate(pages, 1):
            for m in re.finditer(rx, t, re.I):
                hits.append((m.group(1).strip(), i, _line(t, m.start()), bool(QUAL.search(m.group(0)))))
        terms[name] = _build(name, hits, _label(name, pages))
    hits = []
    for i, t in enumerate(pages, 1):
        for m in re.finditer(r"\b(fixed|floating)\b", t, re.I):
            ln = _line(t, m.start())
            if "interest" in ln.lower() or "rate" in ln.lower():
                hits.append((m.group(1).lower(), i, ln, False))
    terms["interest_type"] = _build("interest_type", hits)
    return terms


def _parse_contract_date(raw):
    """Normalize an explicit date only when its source format has one clear reading."""
    value = raw.strip().rstrip(".,")
    try:
        return date.fromisoformat(value).isoformat(), None
    except ValueError:
        pass
    month_text = value.replace(",", "")
    for pattern in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(month_text, pattern).date().isoformat(), None
        except ValueError:
            continue
    numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", value)
    if numeric:
        first, second, year = (int(part) for part in numeric.groups())
        if first <= 12 and second <= 12:
            return None, "Numeric date has an ambiguous day/month order; confirm the date from the source."
        if first > 12 and 1 <= second <= 12:
            try:
                return date(year, second, first).isoformat(), None
            except ValueError:
                pass
    return None, "Date could not be normalized safely; confirm it from the source."

def _build(name, hits, label=None):
    if not hits:
        if label:
            return {"value": None, "raw": None, "page": label[0], "evidence": label[1], "status": "review",
                    "note": "Field label found but no usable value (ambiguous or deferred wording)."}
        return {"value": None, "raw": None, "page": None, "evidence": None, "status": "missing"}
    raw, page, ev, _q = hits[0]
    if name in DATE_FIELDS:
        normalized = [_parse_contract_date(hit[0]) for hit in hits]
        candidates = [
            {"raw": hit[0], "value": value, "page": hit[1], "evidence": hit[2],
             "status": "review" if note or hit[3] else "extracted"}
            for hit, (value, note) in zip(hits, normalized)
        ]
        valid_dates = {value for value, _note in normalized if value is not None}
        notes = [note for _value, note in normalized if note]
        if notes:
            value, note = None, notes[0]
            status = "review"
        elif len(valid_dates) > 1:
            candidates_text = "; ".join(
                f"{candidate['value']} (page {candidate['page']}: {candidate['evidence']})"
                for candidate in candidates
            )
            value, note = None, "Multiple differing dates found: " + candidates_text + ". Confirm the governing date."
            status = "review"
        else:
            value, note = (next(iter(valid_dates)) if valid_dates else None), None
            status = "extracted"
        if any(hit[3] for hit in hits):
            status = "review"
            note = "Date is qualified or conditional; confirm it against the source."
        result = {"value": value, "raw": raw, "page": page, "evidence": ev,
                  "status": status, "note": note}
        if len(candidates) > 1:
            result["candidates"] = candidates
        return result
    if name in ("loan_amount", "emi"): val = pnum(raw)
    elif name == "interest_rate": val = float(raw)
    elif name == "tenure":
        n = int(pnum(raw)); val = n * 12 if "year" in raw.lower() else n
    else: val = raw
    distinct = {h[0].lower().replace(" ", "") for h in hits}
    qualified = any(h[3] for h in hits)
    status = "extracted" if len(distinct) == 1 and not qualified else "review"
    note = None
    if len(distinct) > 1: note = "Multiple differing values found: " + ", ".join(sorted(distinct))
    elif qualified: note = "Value is qualified (approximate, indicative or conditional)."
    return {"value": val, "raw": raw, "page": page, "evidence": ev, "status": status, "note": note}

def extract_clauses(pages):
    out, seen = [], set()
    for i, t in enumerate(pages, 1):
        heading = None
        for ln in (l.strip() for l in t.split("\n")):
            if not ln: continue
            if len(ln) < 60 and (ln.isupper() or re.match(r"^\d+(\.\d+)*[\.\)]?\s+[A-Z]", ln)): heading = ln
            if len(ln) < 25: continue
            low = ln.lower()
            for cat, kws in ORDERED:
                if _kw(low, kws) and (i, ln) not in seen:
                    seen.add((i, ln))
                    ex, att = EXPLAIN[cat]
                    out.append({"category": cat, "text": ln, "page": i, "section": heading,
                                "explanation": ex + " (rule-based summary, not a generated interpretation)",
                                "attention_point": att, "evidence": ln})
                    break
    return out
