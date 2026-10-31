"""Conservative, evidence-preserving extraction for insurance policy documents."""
import re
from datetime import date, datetime


_DATE = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?[,]?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{4})"
_AMOUNT = r"((?:(?:INR|Rs\.?|₹)\s*)?[\d,]+(?:\.\d{1,2})?(?:\s*(?:INR|Rs\.?))?)"

POLICY_TEXT_FIELDS = {"policy_number", "insurer", "product_name", "policyholder"}
POLICY_MONEY_FIELDS = {"sum_insured", "premium_amount"}
POLICY_DATE_FIELDS = {"policy_start_date", "policy_expiry_date"}
POLICY_FIELDS = POLICY_TEXT_FIELDS | POLICY_MONEY_FIELDS | POLICY_DATE_FIELDS
POLICY_CORRECTABLE_FIELDS = POLICY_FIELDS

_FIELD_SPECS = {
    "policy_number": (r"(?:policy\s*(?:no\.?|number)|certificate\s*no\.?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/-]{3,39})", "text"),
    "insurer": (r"(?:name\s+of\s+insurer|insurance\s+company|insurer|underwriter)\s*[:#-]\s*([^\n]{2,100})", "text"),
    "product_name": (r"(?:product\s+name|insurance\s+product|plan\s+name|policy\s+type)\s*[:#-]\s*([^\n]{2,100})", "text"),
    "policyholder": (r"(?:policy\s*holder|policyholder|name\s+of\s+insured|insured\s+name)\s*[:#-]\s*([^\n]{2,100})", "text"),
    "sum_insured": (r"(?:sum\s+insured|sum\s+assured|coverage\s+amount|maximum\s+coverage)\s*[:#-]?\s*" + _AMOUNT, "money"),
    "premium_amount": (r"(?:total\s+premium|annual\s+premium|premium\s+(?:amount|payable))\s*[:#-]?\s*" + _AMOUNT, "money"),
    "policy_start_date": (r"(?:policy\s+(?:start|commencement|effective)\s+date|coverage\s+start\s+date|effective\s+from)\s*[:#-]?\s*(" + _DATE + ")", "date"),
    "policy_expiry_date": (r"(?:policy\s+(?:expiry|expiration|end)\s+date|coverage\s+end\s+date|renewal\s+(?:due\s+)?date|valid\s+until)\s*[:#-]?\s*(" + _DATE + ")", "date"),
}
_LABELS = {
    "policy_number": r"policy\s*(?:no\.?|number)|certificate\s*no\.?",
    "insurer": r"name\s+of\s+insurer|insurance\s+company|insurer|underwriter",
    "product_name": r"product\s+name|insurance\s+product|plan\s+name|policy\s+type",
    "policyholder": r"policy\s*holder|policyholder|name\s+of\s+insured|insured\s+name",
    "sum_insured": r"sum\s+insured|sum\s+assured|coverage\s+amount|maximum\s+coverage",
    "premium_amount": r"total\s+premium|annual\s+premium|premium\s+(?:amount|payable)",
    "policy_start_date": r"policy\s+(?:start|commencement|effective)\s+date|coverage\s+start\s+date|effective\s+from",
    "policy_expiry_date": r"policy\s+(?:expiry|expiration|end)\s+date|coverage\s+end\s+date|renewal\s+(?:due\s+)?date|valid\s+until",
}


def is_policy_document(pages):
    """Return true only for a policy title or multiple explicit policy identifiers."""
    if not pages:
        return False
    title_lines = [line.strip() for line in pages[0].splitlines() if line.strip()][:6]
    title = "\n".join(title_lines[:4])[:500]
    if re.search(r"(?i)\b(?:loan\s+agreement|key\s+facts\s+statement|sanction\s+letter)\b", title):
        return False
    if re.search(r"(?im)^.{0,100}\b(?:insurance\s+policy|policy\s+schedule|certificate\s+of\s+insurance|insurance\s+certificate|insurance\s+renewal\s+notice)\b", title):
        return True
    first_page = pages[0][:5000]
    if not re.search(r"(?i)\b(?:insurance|insurer|insured)\b", first_page):
        return False
    explicit_labels = (
        r"\bpolicy\s*(?:no\.?|number)\b",
        r"\b(?:sum\s+insured|sum\s+assured)\b",
        r"\b(?:annual\s+premium|premium\s+(?:amount|payable))\b",
        r"\b(?:insurance\s+company|name\s+of\s+insurer)\b",
    )
    return sum(bool(re.search(pattern, first_page, re.I)) for pattern in explicit_labels) >= 2


def _line(text, position):
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    return text[start:len(text) if end < 0 else end].strip()


def _date_value(raw):
    value = raw.strip().rstrip(".,")
    try:
        return date.fromisoformat(value), None
    except ValueError:
        pass
    for pattern in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value.replace(",", ""), pattern).date(), None
        except ValueError:
            continue
    numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", value)
    if numeric:
        first, second, year = (int(part) for part in numeric.groups())
        if first <= 12 and second <= 12:
            return None, "Numeric date has an ambiguous day/month order; confirm it from the source."
        day, month = (first, second) if first > 12 else (second, first)
        try:
            return date(year, month, day), None
        except ValueError:
            pass
    return None, "Date could not be normalized safely; confirm it from the source."


def _normalize(raw, kind, default_currency=None):
    if kind == "text":
        return re.sub(r"\s+", " ", raw.strip().rstrip(" ,;")), None
    if kind == "money":
        match = re.search(r"[\d,]+(?:\.\d{1,2})?", raw)
        if not match:
            return None, "Amount could not be normalized safely; confirm it from the source."
        marker = re.search(r"(?:\bINR\b|\bRs\.?\b|₹)", raw, re.I)
        currency = "INR" if marker or default_currency == "INR" else None
        if currency is None:
            return None, "Currency is not explicit; confirm the amount and currency from the source."
        try:
            value = float(match.group().replace(",", ""))
        except ValueError:
            return None, "Amount could not be normalized safely; confirm it from the source."
        if value < 0 or value > 1_000_000_000_000:
            return None, "Amount is outside the supported range; confirm it from the source."
        return value, None
    parsed, error = _date_value(raw)
    return (parsed.isoformat(), None) if parsed else (None, error)


def _extract_field(name, pages):
    pattern, kind = _FIELD_SPECS[name]
    hits = []
    for page_number, page in enumerate(pages, 1):
        for match in re.finditer(pattern, page, re.I):
            raw = match.group(1).strip()
            value, error = _normalize(raw, kind, _document_currency(pages))
            hits.append({"raw": raw, "value": value, "error": error,
                         "currency": "INR" if kind == "money" and not error else None,
                         "page": page_number, "evidence": _line(page, match.start())})
    if not hits:
        label = re.compile(r"^\s*(?:" + _LABELS[name] + r")\b[^:\n]{0,50}:", re.I | re.M)
        for page_number, page in enumerate(pages, 1):
            match = label.search(page)
            if match:
                evidence = _line(page, match.end())
                return {"value": None, "raw": None, "page": page_number, "evidence": evidence,
                        "status": "review", "note": "Field label found but no usable value was extracted."}
        return {"value": None, "raw": None, "page": None, "evidence": None, "status": "missing"}

    first = hits[0]
    values = {re.sub(r"\s+", " ", str(hit["value"])).casefold() for hit in hits if hit["value"] is not None}
    errors = [hit["error"] for hit in hits if hit["error"]]
    qualified = any(re.search(r"\b(?:approx(?:imately)?|estimated|indicative|subject\s+to|up\s+to)\b", hit["evidence"], re.I) for hit in hits)
    note = errors[0] if errors else None
    status = "extracted"
    if errors or len(values) != 1 or qualified:
        status = "review"
        if len(values) > 1:
            note = "Multiple differing values found; confirm the governing value from the source."
        elif qualified:
            note = "Value is qualified or conditional; confirm it against the source."
    value = first["value"] if status == "extracted" else None
    result = {"value": value, "raw": first["raw"], "page": first["page"], "evidence": first["evidence"],
              "status": status, "note": note}
    if kind == "money":
        result["currency"] = first["currency"]
    return result


def _document_currency(pages):
    for page in pages:
        if re.search(r"(?i)(?:currency|all\s+(?:amounts|figures)\s+in)\s*[:#-]?\s*(?:INR\b|Rs\.?\b|\u20b9)", page):
            return "INR"
    return None

def extract_policy_terms(pages):
    """Extract a small set of clearly labeled policy fields, leaving other values missing."""
    return {name: _extract_field(name, pages) for name in _FIELD_SPECS}


_CLAUSE_RULES = (
    ("Exclusions", ("exclusion", "not covered", "excluded"), "Lists circumstances or items that may not be covered.", "Confirm that exclusions and limits are understood for the planned use."),
    ("Waiting periods", ("waiting period", "initial waiting"), "Describes a period before some coverage may apply.", "Check when each stated benefit becomes available."),
    ("Claims and notice", ("claim", "intimation", "notify the insurer"), "Describes claim or notification steps.", "Check required documents, deadlines, and where a claim must be sent."),
    ("Premium and renewal", ("premium", "renewal", "grace period", "lapse"), "Describes premium payment or continuation of cover.", "Confirm the renewal date, amount, and consequences of late payment."),
    ("Cancellation and refund", ("cancellation", "free look", "surrender", "refund"), "Describes cancellation or refund conditions.", "Check the time window and any stated deductions."),
    ("Coverage and limits", ("coverage", "sum insured", "sum assured", "benefit limit", "deductible", "co-payment"), "Describes a coverage limit, share, or benefit.", "Compare the limit and any customer share with the policy schedule."),
)


def extract_policy_clauses(pages):
    results, seen = [], set()
    for page_number, page in enumerate(pages, 1):
        heading = None
        for line in (item.strip() for item in page.splitlines()):
            if not line:
                continue
            if len(line) < 100 and (line.isupper() or re.match(r"^\d+(?:\.\d+)*[.)]?\s+[A-Z]", line)):
                heading = line
            if len(line) < 25 or len(line) > 1500:
                continue
            lower = line.casefold()
            for category, keywords, explanation, attention in _CLAUSE_RULES:
                if any(keyword in lower for keyword in keywords) and (page_number, line) not in seen:
                    seen.add((page_number, line))
                    results.append({
                        "category": category, "text": line, "page": page_number,
                        "section": heading, "explanation": explanation + " (rule-based summary, not a policy interpretation)",
                        "attention_point": attention, "evidence": line,
                    })
                    break
    return results
