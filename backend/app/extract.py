"""Rule-based classification, field extraction and clause intelligence (no LLM; transparent regexes)."""
import re

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

QUAL = re.compile(r"approx|about|up to|estimated|indicative|subject to|not exceeding", re.I)
LABELS = {"loan_amount": r"loan amount|sanctioned amount|principal amount", "interest_rate": r"rate of interest|interest rate",
          "tenure": r"tenure", "emi": r"equated monthly instal?lment|monthly instal?lment|emi", "processing_fee": r"processing fee"}

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

def _build(name, hits, label=None):
    if not hits:
        if label:
            return {"value": None, "raw": None, "page": label[0], "evidence": label[1], "status": "review",
                    "note": "Field label found but no usable value (ambiguous or deferred wording)."}
        return {"value": None, "raw": None, "page": None, "evidence": None, "status": "missing"}
    raw, page, ev, _q = hits[0]
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
