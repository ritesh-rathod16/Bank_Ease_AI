"""Small, explainable checks for explicitly labeled financial-statement totals."""
from decimal import Decimal, InvalidOperation
from datetime import datetime
import re


_TITLE = re.compile(
    r"(?i)^\s*(?:balance sheet|statement of financial position|statement of profit and loss|"
    r"statement of profit or loss|profit and loss statement|income statement|"
    r"cash flow statement|statement of cash flows|annual financial statements?)\b"
)
_FIELDS = {
    "total_assets": re.compile(r"(?i)^\s*(?:total\s+assets|assets\s*,?\s*total)\b"),
    "total_liabilities_and_equity": re.compile(
        r"(?i)^\s*(?:total\s+(?:liabilities\s*(?:and|&)\s*(?:equity|shareholders'?\s+funds)|"
        r"equity\s+and\s+liabilities)|total\s+equity\s+and\s+liabilities)\b"
    ),
    "total_liabilities": re.compile(r"(?i)^\s*(?:total\s+liabilities|liabilities\s*,?\s*total)\b"),
    "total_equity": re.compile(
        r"(?i)^\s*(?:total\s+equity|shareholders'?\s+(?:equity|funds)|owners'?\s+equity|net\s+worth)\b"
    ),
    "total_revenue": re.compile(r"(?i)^\s*(?:total\s+revenue|revenue\s+from\s+operations)\b"),
    "total_income": re.compile(r"(?i)^\s*total\s+income\b"),
    "total_expenses": re.compile(r"(?i)^\s*(?:total\s+expenses|total\s+expenditure)\b"),
    "profit_after_tax": re.compile(
        r"(?i)^\s*(?:profit\s+after\s+tax|net\s+profit\s+after\s+tax|profit\s+for\s+the\s+year)\b"
    ),
}
_NUMBER = re.compile(r"(?<![A-Za-z0-9])(?:\(\s*\d[\d,]*(?:\.\d+)?\s*\)|-?\d[\d,]*(?:\.\d+)?)(?![A-Za-z0-9])")
_UNIT_CURRENCY = re.compile(r"(?i)(?<![A-Z])(?:INR|USD|US\$|EUR|GBP|AUD|CAD|JPY|₹|€|£)(?![A-Z])")
_UNIT_SCALE = re.compile(r"(?i)\b(rupees?|units?|thousands?|lakhs?|crores?|millions?|billions?)\b")
_PERIOD_DATE = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
_PERIOD_LABEL = re.compile(
    rf"(?i)\b(?P<basis>as at|as of|(?:for (?:the )?)?year ended|(?:for (?:the )?)?period ended)\s+(?P<date>{_PERIOD_DATE})\b"
)

COMPARISON_FIELDS = (
    ("total_assets", "Total assets"),
    ("total_liabilities_and_equity", "Total liabilities and equity"),
    ("total_liabilities", "Total liabilities"),
    ("total_equity", "Total equity"),
    ("total_revenue", "Total revenue"),
    ("total_income", "Total income"),
    ("total_expenses", "Total expenses"),
    ("profit_after_tax", "Profit after tax"),
)


def is_financial_statement(pages):
    if not pages:
        return False
    lines = [line.strip() for line in pages[0].splitlines() if line.strip()][:5]
    title = next((line for line in lines if _TITLE.search(line)), "")
    if not title:
        return False
    first_page = "\n".join(pages[0].splitlines()[:100])
    return sum(any(pattern.search(line) for line in first_page.splitlines()) for pattern in _FIELDS.values()) >= 2


def _number_value(token):
    negative = token.startswith("(") and token.endswith(")")
    number = token.strip("() ").replace(",", "")
    original = token.strip("() ")
    integer_group = original.lstrip("-").split(".", 1)[0]
    if "," in integer_group:
        groups = integer_group.split(",")
        if not (1 <= len(groups[0]) <= 3 and len(groups[-1]) == 3
                and all(len(group) in {2, 3} for group in groups[1:-1])):
            return None
    try:
        value = Decimal(number)
    except InvalidOperation:
        return None
    if not value.is_finite() or abs(value) > Decimal("1000000000000000000"):
        return None
    if negative:
        value = -abs(value)
    return format(value.normalize(), "f") if value else "0"


def _read_amount(line, next_line=""):
    cleaned = re.sub(r"\(?\s*(?:note|ref)\.?\s+\d+\s*\)?", "", line, flags=re.I)
    tokens = _NUMBER.findall(cleaned)
    if not tokens and next_line.strip():
        tokens = _NUMBER.findall(next_line.strip())
        evidence = f"{line.strip()} | {next_line.strip()}"
    else:
        evidence = line.strip()
    if len(tokens) != 1:
        return None, evidence, "Expected one unambiguous amount on the labeled row; check comparative columns and the original."
    value = _number_value(tokens[0])
    if value is None:
        return None, evidence, "Amount could not be normalized safely; check the original statement."
    return value, evidence, None


def extract_fields(pages):
    terms = {}
    for field, label in _FIELDS.items():
        hits, labeled = [], None
        for page_number, page in enumerate(pages, 1):
            lines = page.splitlines()
            for index, line in enumerate(lines):
                match = label.search(line)
                if not match:
                    continue
                if labeled is None:
                    labeled = (page_number, line.strip()[:320])
                tail = line[match.end():].strip(" .:|\t")
                row = f"{line[:match.end()].strip()} {tail}".strip()
                next_line = lines[index + 1] if not tail and index + 1 < len(lines) else ""
                value, _parsed_evidence, issue = _read_amount(row, next_line)
                evidence = f"{line.strip()} | {next_line.strip()}" if next_line else line.strip()
                hits.append({"value": value, "page": page_number, "raw": tail or next_line.strip(),
                             "evidence": evidence[:320], "issue": issue})
        if not hits:
            if labeled:
                terms[field] = {"value": None, "raw": None, "page": labeled[0], "evidence": labeled[1],
                                "status": "review", "note": "Amount label found without one readable value."}
            else:
                terms[field] = {"value": None, "raw": None, "page": None, "evidence": None, "status": "missing"}
            continue
        valid = {hit["value"] for hit in hits if hit["value"] is not None}
        issues = [hit["issue"] for hit in hits if hit["issue"]]
        if issues or len(valid) > 1:
            note = issues[0] if issues else "Conflicting amounts were found; check the cited pages."
            value, status = None, "review"
        elif not valid:
            note, value, status = "Amount could not be read safely; check the original.", None, "review"
        else:
            note, value, status = None, next(iter(valid)), "extracted"
        first = hits[0]
        terms[field] = {"value": value, "raw": first["raw"] or None, "page": first["page"],
                        "evidence": first["evidence"], "status": status, "note": note}
    return terms


def verify_totals(terms):
    """Validate a same-page accounting equation without assuming currency or scale."""
    direct = ["total_assets", "total_liabilities_and_equity"]
    separate = ["total_assets", "total_liabilities", "total_equity"]
    active = direct if all((terms.get(name) or {}).get("status") == "extracted" for name in direct) else separate
    sources = []
    for name in active:
        term = terms.get(name) or {}
        corrected = term.get("user_correction")
        value = term.get("effective_value") if corrected and term.get("effective_value") is not None else term.get("value")
        if (term.get("status") not in {"extracted", "manual"} and not corrected) or value is None:
            return [{"id": "balance_sheet_equation", "label": "Assets equal liabilities and equity",
                     "status": "not_evaluated", "summary": "Not checked: one or more labeled totals need review.", "sources": sources}]
        sources.append({"field": name, "value": str(value), "page": term.get("page"), "evidence": term.get("evidence")})
    if len({source["page"] for source in sources}) != 1:
        return [{"id": "balance_sheet_equation", "label": "Assets equal liabilities and equity",
                 "status": "not_evaluated", "summary": "Not checked: totals appear on different pages.", "sources": sources}]
    values = [Decimal(source["value"]) for source in sources]
    difference = values[0] - (values[1] if len(active) == 2 else values[1] + values[2])
    status = "consistent" if abs(difference) <= Decimal("0.01") else "difference"
    return [{"id": "balance_sheet_equation", "label": "Assets equal liabilities and equity", "status": status,
             "difference": format(difference.normalize(), "f") if difference else "0",
             "summary": "The labeled totals balance." if status == "consistent" else "The labeled totals differ; verify them against the original.",
             "sources": sources}]


def _comparison_unit(evidence):
    """Return a reporting unit only when both currency and scale are printed on the cited row."""
    text = evidence or ""
    currency = _UNIT_CURRENCY.search(text)
    scale = _UNIT_SCALE.search(text)
    if not currency or not scale:
        return None
    currency_token = currency.group().casefold()
    currency_code = {"₹": "inr", "€": "eur", "£": "gbp", "us$": "usd"}.get(currency_token, currency_token)
    scale_token = scale.group().casefold().rstrip("s")
    return currency_code, scale_token


def extract_reporting_period(pages):
    """Find one explicit, unambiguous period label; never infer dates from filenames."""
    candidates = {}
    for page_number, page in enumerate(pages or [], 1):
        for match in _PERIOD_LABEL.finditer(page):
            date_text = match.group("date").strip().rstrip(",")
            parsed = None
            for date_format in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%B %d, %Y", "%b %d %Y", "%b %d, %Y"):
                try:
                    parsed = datetime.strptime(date_text, date_format).date()
                    break
                except ValueError:
                    continue
            if parsed is None:
                continue
            basis_text = match.group("basis").casefold()
            basis = "as_at" if basis_text.startswith("as ") else "year_ended" if "year ended" in basis_text else "period_ended"
            key = (basis, parsed.isoformat())
            evidence = match.group(0).strip()
            candidates.setdefault(key, {"date": parsed.isoformat(), "basis": basis,
                                        "page": page_number, "evidence": evidence})
    if not candidates:
        return {"value": None, "date": None, "basis": None, "page": None,
                "evidence": None, "status": "not_found"}
    if len(candidates) != 1:
        return {"value": None, "date": None, "basis": None, "page": None,
                "evidence": None, "status": "ambiguous"}
    candidate = next(iter(candidates.values()))
    candidate["value"] = f"{candidate['basis'].replace('_', ' ')} · {candidate['date']}"
    candidate["status"] = "explicit"
    return candidate


def compare_statements(document_a, terms_a, document_b, terms_b):
    """Compare explicitly labeled values; calculate deltas only for matching cited units."""
    rows = []
    summary = {"comparable_field_count": 0, "difference_count": 0,
               "needs_review_count": 0, "not_comparable_count": 0}
    for field, label in COMPARISON_FIELDS:
        sides = []
        for terms in (terms_a, terms_b):
            term = terms.get(field) or {}
            status = term.get("status", "missing")
            value = term.get("effective_value", term.get("value"))
            source = {"value": str(value) if value is not None else None,
                      "raw": term.get("raw"), "page": term.get("page"),
                      "evidence": term.get("evidence"), "status": status,
                      "manual": status == "manual"}
            sides.append(source)

        a, b = sides
        row_status = "not_found"
        delta = None
        unit = None
        if a["status"] == "review" or b["status"] == "review":
            row_status = "needs_review"
            summary["needs_review_count"] += 1
        elif a["value"] is not None and b["value"] is not None:
            unit_a, unit_b = _comparison_unit(a["evidence"]), _comparison_unit(b["evidence"])
            if unit_a is None or unit_b is None or unit_a != unit_b:
                row_status = "not_comparable"
                summary["not_comparable_count"] += 1
            else:
                try:
                    value_a, value_b = Decimal(a["value"]), Decimal(b["value"])
                except (InvalidOperation, TypeError):
                    row_status = "needs_review"
                    summary["needs_review_count"] += 1
                else:
                    if not value_a.is_finite() or not value_b.is_finite():
                        row_status = "needs_review"
                        summary["needs_review_count"] += 1
                    else:
                        delta = format((value_b - value_a).normalize(), "f")
                        row_status = "match" if value_a == value_b else "potential_difference"
                        unit = {"currency": unit_a[0], "scale": unit_a[1]}
                        summary["comparable_field_count"] += 1
                        summary["difference_count"] += row_status == "potential_difference"
        rows.append({"field": field, "label": label, "status": row_status,
                     "a": a, "b": b, "difference_b_minus_a": delta, "unit": unit})
    return {
        "a": {"id": document_a["id"], "filename": document_a["filename"],
              "reporting_period": extract_reporting_period(document_a.get("pages", []))},
        "b": {"id": document_b["id"], "filename": document_b["filename"],
              "reporting_period": extract_reporting_period(document_b.get("pages", []))},
        "note": ("A reporting period is shown only when one explicit date label is found. Values are compared only "
                 "when the cited rows explicitly show the same currency and scale; table-header units are not inferred."),
        "summary": summary,
        "rows": rows,
    }
