"""Exact, evidence-preserving comparison of imported bank statements."""
from collections import defaultdict


def _signature(row):
    effective = row.get("effective") or row
    description = " ".join(str(effective.get("description", "")).casefold().split())
    return effective.get("date"), int(effective.get("amount_minor", 0)), description


def _source(row):
    return {
        "row_number": row.get("row_number"),
        "source_sheet": row.get("source_sheet"),
        "page": row.get("page"),
        "evidence": row.get("evidence"),
        "source_values": row.get("source_values", {}),
        "effective": row.get("effective"),
        "correction_revision": row.get("correction_revision", 0),
        "corrected": bool(row.get("user_correction")),
    }


def _effective(row, field):
    return (row.get("effective") or row).get(field)


def _document(document):
    analysis = document.get("statement_analysis", {})
    return {
        "id": document["id"],
        "filename": document["filename"],
        "currency": document.get("currency") or analysis.get("currency", "UNSPECIFIED"),
        "transaction_count": int(document.get("transaction_count", 0)),
        "balance_check": analysis.get("balance_check", "unavailable"),
        "opening_balance_minor": analysis.get("opening_balance_minor"),
        "closing_balance_minor": analysis.get("closing_balance_minor"),
    }


def compare_statements(document_a, transactions_a, document_b, transactions_b):
    """Compare exact transaction multisets; mismatches remain human-review signals."""
    a, b = _document(document_a), _document(document_b)
    comparable = a["currency"] != "UNSPECIFIED" and a["currency"] == b["currency"]
    note = (
        "Transactions are compared by effective date, signed amount, and description, using saved corrections where present. Balance summaries remain based on imported source data. Differences are review signals, not proof of error or misconduct."
        if comparable else
        "Amounts were not compared because both statements need the same identified currency. Check the source statements and currency labels."
    )
    if not comparable:
        return {
            "a": a, "b": b, "comparable": False, "note": note,
            "summary": {
                "matched_transaction_count": 0,
                "only_in_a_count": 0,
                "only_in_b_count": 0,
                "running_balance_difference_count": 0,
                "difference_count": 0,
            },
            "balance_comparison": {},
            "differences": [],
        }

    rows_a, rows_b = defaultdict(list), defaultdict(list)
    for row in transactions_a:
        rows_a[_signature(row)].append(row)
    for row in transactions_b:
        rows_b[_signature(row)].append(row)
    for rows in (rows_a, rows_b):
        for grouped_rows in rows.values():
            grouped_rows.sort(key=lambda row: row.get("row_number", 0))

    matched_count = only_a_count = only_b_count = running_balance_difference_count = 0
    differences = []
    for signature in sorted(set(rows_a) | set(rows_b), key=lambda item: (item[0] or "", item[1], item[2])):
        left, right = rows_a.get(signature, []), rows_b.get(signature, [])
        paired = min(len(left), len(right))
        matched_count += paired
        for row_a, row_b in zip(left[:paired], right[:paired], strict=True):
            balance_a = _effective(row_a, "balance_minor")
            balance_b = _effective(row_b, "balance_minor")
            if balance_a is not None and balance_b is not None and balance_a != balance_b:
                running_balance_difference_count += 1
                differences.append({
                    "kind": "running_balance_difference",
                    "date": signature[0],
                    "amount_minor": signature[1],
                    "description": _effective(row_a, "description"),
                    "a": _source(row_a),
                    "b": _source(row_b),
                })
        for row in left[paired:]:
            only_a_count += 1
            differences.append({
                "kind": "only_in_a", "date": signature[0],
                "amount_minor": signature[1], "description": row.get("description", ""),
                "a": _source(row), "b": None,
            })
        for row in right[paired:]:
            only_b_count += 1
            differences.append({
                "kind": "only_in_b", "date": signature[0],
                "amount_minor": signature[1], "description": row.get("description", ""),
                "a": None, "b": _source(row),
            })

    balance_comparison = {}
    for field in ("opening_balance_minor", "closing_balance_minor"):
        value_a, value_b = a[field], b[field]
        if value_a is not None and value_b is not None:
            balance_comparison[field] = {
                "a_minor": value_a,
                "b_minor": value_b,
                "difference_minor": value_b - value_a,
                "a_is_implied": field == "opening_balance_minor",
                "b_is_implied": field == "opening_balance_minor",
            }

    differences.sort(key=lambda item: (
        item["date"], item["kind"],
        (item["a"] or item["b"] or {}).get("row_number", 0),
    ))
    return {
        "a": a,
        "b": b,
        "comparable": True,
        "note": note,
        "summary": {
            "matched_transaction_count": matched_count,
            "only_in_a_count": only_a_count,
            "only_in_b_count": only_b_count,
            "running_balance_difference_count": running_balance_difference_count,
            "difference_count": len(differences),
        },
        "balance_comparison": balance_comparison,
        "differences": differences,
    }
