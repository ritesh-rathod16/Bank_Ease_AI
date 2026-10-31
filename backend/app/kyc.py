"""Conservative, source-linked extraction for explicitly titled KYC forms.

This module does not identify people, extract government ID numbers, or verify
identity. It only reads three fields when they are explicitly labeled in a
KYC/customer-identification form.
"""
import re

from . import extract


KYC_TEXT_FIELDS = {"full_name", "address"}
KYC_DATE_FIELDS = {"date_of_birth"}
KYC_FIELDS = KYC_TEXT_FIELDS | KYC_DATE_FIELDS

_TITLE = re.compile(
    r"(?i)\b(?:know\s+your\s+customer|KYC\s+(?:form|application|documents?)|"
    r"customer\s+(?:identification|due\s+diligence)\s+form|account\s+opening\s+form)\b"
)
_LABELS = {
    "full_name": re.compile(
        r"(?i)^\s*(?:full\s+name|name\s+of\s+(?:the\s+)?(?:applicant|customer)|"
        r"applicant\s+name|customer\s+name)\s*[:#-]\s*(.*?)\s*$"
    ),
    "date_of_birth": re.compile(
        r"(?i)^\s*(?:date\s+of\s+birth|birth\s+date|DOB)\s*[:#-]\s*(.*?)\s*$"
    ),
    "address": re.compile(
        r"(?i)^\s*(?:(?:current|permanent|residential|communication)\s+)?address\s*[:#-]\s*(.*?)\s*$"
    ),
}


def is_kyc_form(pages):
    """Require an explicit form title and at least two labeled fields."""
    if not pages:
        return False
    title = "\n".join([line.strip() for line in pages[0].splitlines() if line.strip()][:5])[:500]
    if not _TITLE.search(title):
        return False
    if re.search(r"(?i)\b(?:loan\s+agreement|insurance\s+policy|bank\s+statement|account\s+statement)\b", title):
        return False
    first_page = pages[0][:8000]
    return sum(
        any(pattern.match(line) for line in first_page.splitlines())
        for pattern in _LABELS.values()
    ) >= 2


def extract_kyc_fields(pages):
    """Return only explicit, short labeled values and evidence on the same line."""
    terms = {}
    for field, pattern in _LABELS.items():
        hits = []
        label_seen = None
        for page_number, page in enumerate(pages, 1):
            for line in page.splitlines():
                match = pattern.match(line)
                if not match:
                    continue
                evidence = line.strip()[:320]
                value = match.group(1).strip()[:120]
                if label_seen is None:
                    label_seen = (page_number, evidence)
                if value:
                    hits.append((value, page_number, evidence))

        if not hits:
            if label_seen:
                terms[field] = {
                    "value": None, "raw": None, "page": label_seen[0],
                    "evidence": label_seen[1], "status": "review",
                    "note": "Field label found without a readable value; check the original form.",
                }
            else:
                terms[field] = {"value": None, "raw": None, "page": None,
                                "evidence": None, "status": "missing"}
            continue

        raw, page_number, evidence = hits[0]
        if field == "date_of_birth":
            normalized = [extract._parse_contract_date(hit[0]) for hit in hits]
            valid = {value for value, note in normalized if value is not None and note is None}
            notes = [note for _value, note in normalized if note]
            if notes:
                value, status, note = None, "review", notes[0]
            elif len(valid) > 1:
                value, status = None, "review"
                note = "Conflicting birth dates were found; check the cited pages."
            elif not valid:
                value, status = None, "review"
                note = "Birth date could not be normalized safely; check the original form."
            else:
                value, status, note = next(iter(valid)), "extracted", None
        else:
            distinct = {re.sub(r"\s+", " ", hit[0]).strip().casefold() for hit in hits}
            if len(distinct) > 1:
                value, status = None, "review"
                note = "Conflicting labeled values were found; check the cited pages."
            else:
                value = re.sub(r"\s+", " ", raw).strip(" ,;:")
                status, note = "extracted", None
        terms[field] = {
            "value": value, "raw": raw, "page": page_number,
            "evidence": evidence, "status": status, "note": note,
        }
    return terms
