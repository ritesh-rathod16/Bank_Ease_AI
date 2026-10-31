"""Evidence-preserving extraction from explicitly titled audit reports."""
import re


_TITLE = re.compile(
    r"(?i)^\s*(?:independent\s+auditor(?:s|['’]s?)?\s+report|auditor(?:s|['’]s?)?\s+report|"
    r"(?:internal|statutory|financial)\s+audit\s+report|audit\s+report)\b"
)
_SECTION_HEADINGS = {
    "Auditor opinion": re.compile(r"(?i)^\s*(?:auditor(?:s|['’]s?)?\s+opinion|opinion)\s*:?\s*$"),
    "Basis for opinion": re.compile(r"(?i)^\s*basis\s+for\s+(?:qualified\s+)?opinion\s*:?\s*$"),
    "Key audit matters": re.compile(r"(?i)^\s*key\s+audit\s+matters?\s*:?\s*$"),
    "Emphasis of matter": re.compile(r"(?i)^\s*emphasis\s+of\s+matter\s*:?\s*$"),
    "Audit findings": re.compile(r"(?i)^\s*(?:audit\s+findings?|internal\s+control\s+(?:findings?|observations?)|management\s+letter\s+(?:points?|findings?))\s*:?\s*$"),
}
_FIELD_LABELS = {
    "auditor_name": re.compile(r"(?i)^\s*(?:name\s+of\s+auditor|audit\s+firm|auditor\s+firm|statutory\s+auditor)\s*:\s*(.*?)\s*$"),
    "reporting_period": re.compile(r"(?i)^\s*(?:reporting\s+period|period\s+covered|financial\s+year\s+ended|for\s+the\s+year\s+ended)\s*:\s*(.*?)\s*$"),
    "report_date": re.compile(r"(?i)^\s*(?:date\s+of\s+report|report\s+date|dated)\s*:\s*(.*?)\s*$"),
    "audit_opinion": re.compile(r"(?i)^\s*(?:audit\s+)?opinion\s*:\s*(.*?)\s*$"),
}


def is_audit_report(pages):
    if not pages:
        return False
    title_lines = [line.strip() for line in pages[0].splitlines() if line.strip()][:5]
    if not any(_TITLE.search(line) for line in title_lines):
        return False
    content = "\n".join(pages[:2])[:12000]
    audit_evidence = (
        r"(?im)^\s*(?:opinion|basis for (?:qualified )?opinion|key audit matters?|"
        r"emphasis of matter|audit findings?|internal control (?:findings?|observations?))\s*:?\s*$"
    )
    return bool(re.search(audit_evidence, content))


def extract_terms(pages):
    result = {}
    for field, pattern in _FIELD_LABELS.items():
        hits = []
        for page_number, page in enumerate(pages, 1):
            for line in page.splitlines():
                match = pattern.match(line)
                if match and match.group(1).strip():
                    hits.append((re.sub(r"\s+", " ", match.group(1).strip())[:120], page_number, line.strip()[:320]))
        if not hits:
            result[field] = {"value": None, "raw": None, "page": None, "evidence": None, "status": "missing"}
            continue
        values = {hit[0].casefold() for hit in hits}
        raw, page_number, evidence = hits[0]
        result[field] = {
            "value": raw if len(values) == 1 else None,
            "raw": raw, "page": page_number, "evidence": evidence,
            "status": "extracted" if len(values) == 1 else "review",
            "note": None if len(values) == 1 else "Conflicting labeled values were found; check the cited pages.",
        }
    return result


def extract_passages(pages):
    passages = []
    for page_number, page in enumerate(pages, 1):
        lines = page.splitlines()
        index = 0
        while index < len(lines):
            heading = next((name for name, pattern in _SECTION_HEADINGS.items() if pattern.match(lines[index])), None)
            if not heading:
                index += 1
                continue
            body, body_lines, cursor = [], 0, index + 1
            while cursor < len(lines) and body_lines < 5:
                text = lines[cursor].strip()
                cursor += 1
                if not text:
                    if body:
                        break
                    continue
                if any(pattern.match(text) for pattern in _SECTION_HEADINGS.values()):
                    cursor -= 1
                    break
                body.append(text[:400])
                body_lines += 1
            if body:
                text = " ".join(body)[:1200]
                passages.append({
                    "category": heading, "text": text, "page": page_number,
                    "section": heading, "evidence": text,
                    "explanation": "Quoted from the report; this is not an independent interpretation.",
                    "attention_point": "Read this passage in the context of the complete report and any management response.",
                })
            index = max(cursor, index + 1)
    return passages
