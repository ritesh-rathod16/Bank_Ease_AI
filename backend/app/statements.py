"""Deterministic, evidence-preserving import for supported banking statements."""
import csv
import io
import math
import re
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation


MAX_ROWS = 5_000
MAX_COLUMNS = 32
MAX_CELL_CHARS = 2_000
MAX_MINOR_UNITS = 9_007_199_254_740_991  # Keep JSON numeric values exact in JavaScript clients.

_HEADER_ALIASES = {
    "date": {"date", "transaction date", "txn date", "tran date", "txn_date", "transaction_date", "posting date", "value date"},
    "description": {"description", "narration", "particulars", "transaction particulars", "transaction details", "remarks"},
    "debit": {"debit", "debit amount", "withdrawal", "withdrawals", "withdrawal amount", "withdrawal amt"},
    "credit": {"credit", "credit amount", "deposit", "deposits", "deposit amount", "deposit amt"},
    "amount": {"amount", "transaction amount", "txn amount"},
    "direction": {"type", "transaction type", "debit credit", "credit debit", "dr cr"},
    "balance": {"balance", "running balance", "closing balance", "available balance"},
    "currency": {"currency", "currency code"},
}
_ALIAS_LOOKUP = {
    re.sub(r"[^a-z0-9]+", " ", alias.casefold()).strip(): field
    for field, aliases in _HEADER_ALIASES.items()
    for alias in aliases
}
_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y",
    "%d-%b-%Y", "%d %b %Y", "%Y%m%d",
)
_CURRENCY_TOKENS = (
    ("₹", "INR"), ("INR", "INR"), ("RS.", "INR"), ("RS", "INR"),
    ("$", "USD"), ("USD", "USD"), ("€", "EUR"), ("EUR", "EUR"),
    ("£", "GBP"), ("GBP", "GBP"),
)


class StatementImportError(ValueError):
    """The CSV is malformed, unsupported, or contains ambiguous values."""


def _normal_header(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _parse_date(value):
    raw = (value or "").strip()
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            pass
    raise StatementImportError("Use an unambiguous date such as YYYY-MM-DD or DD/MM/YYYY.")


def _take_currency(text):
    candidate = text.strip()
    for token, code in _CURRENCY_TOKENS:
        if candidate.upper().startswith(token.upper()):
            return candidate[len(token):].strip(), code
        if candidate.upper().endswith(token.upper()):
            return candidate[:-len(token)].strip(), code
    return candidate, None


def _parse_money(value):
    raw = (value or "").strip()
    if not raw or raw in {"-", "—", "–"}:
        return 0, None
    negative_parentheses = raw.startswith("(") and raw.endswith(")")
    if negative_parentheses:
        raw = raw[1:-1].strip()
    raw, currency = _take_currency(raw)
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d{1,2})?", raw):
        raise StatementImportError("Amounts must use standard digits, comma grouping, and at most two decimals.")
    try:
        amount = Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise StatementImportError("An amount could not be read.") from exc
    if negative_parentheses:
        amount = -abs(amount)
    minor = amount * 100
    if minor != minor.to_integral_value():
        raise StatementImportError("Amounts with fractions smaller than one cent are unsupported.")
    minor_units = int(minor)
    if abs(minor_units) > MAX_MINOR_UNITS:
        raise StatementImportError("Amounts exceed the supported exact numeric range.")
    return minor_units, currency


def _parse_currency(value):
    raw = (value or "").strip()
    if not raw:
        return None
    _, symbol_code = _take_currency(raw)
    if symbol_code:
        return symbol_code
    code = raw.upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise StatementImportError("Currency must be a three-letter code or a supported currency symbol.")
    return code


def _direction(value):
    normalized = re.sub(r"[^a-z]+", " ", (value or "").casefold()).strip()
    if normalized in {"debit", "dr", "withdrawal", "withdraw", "out", "paid"}:
        return "debit"
    if normalized in {"credit", "cr", "deposit", "received", "in"}:
        return "credit"
    raise StatementImportError("Transaction type must clearly identify debit or credit.")


def _map_headers(headers):
    mapped = {}
    for header in headers:
        field = _ALIAS_LOOKUP.get(_normal_header(header))
        if field:
            if field in mapped:
                raise StatementImportError(f"More than one column maps to {field}.")
            mapped[field] = header
    return mapped


def is_statement_header(headers):
    """Return whether a table header identifies a supported transaction layout."""
    try:
        mapped = _map_headers(headers)
    except StatementImportError:
        return False
    split_amounts = "debit" in mapped or "credit" in mapped
    return (
        "date" in mapped and "description" in mapped
        and split_amounts != ("amount" in mapped)
        and (split_amounts or "direction" in mapped)
        and ("debit" in mapped or "credit" in mapped or "amount" in mapped)
    )


def parse_statement_csv(data: bytes):
    """Parse a small CSV statement while retaining row-level source values.

    Dates with slash separators use day/month/year order. Amounts are represented
    as exact integer minor units; currency remains UNSPECIFIED if the source has
    no currency code or supported symbol.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StatementImportError("CSV must be UTF-8 encoded.") from exc
    if not text.strip() or "\x00" in text:
        raise StatementImportError("CSV is empty or contains invalid binary data.")
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames or []
        if not headers or len(headers) > MAX_COLUMNS:
            raise StatementImportError(f"CSV must contain between 1 and {MAX_COLUMNS} columns.")
        mapped = _map_headers(headers)
        if "date" not in mapped or "description" not in mapped:
            raise StatementImportError("CSV needs date and description/narration columns.")
        split_amounts = "debit" in mapped or "credit" in mapped
        if split_amounts == ("amount" in mapped):
            raise StatementImportError("CSV needs debit/credit columns or an amount column with a clear direction.")
        if not split_amounts and "direction" not in mapped:
            raise StatementImportError("Amount-only CSVs need a transaction type column.")
        if "debit" not in mapped and "credit" not in mapped and split_amounts:
            raise StatementImportError("CSV must contain a debit or credit amount column.")

        rows = []
        currencies = set()
        for row_number, row in enumerate(reader, start=2):
            if row_number > MAX_ROWS + 1:
                raise StatementImportError(f"CSV exceeds the {MAX_ROWS} transaction limit.")
            if None in row:
                raise StatementImportError(f"Row {row_number} has more values than the header.")
            source = {str(key).strip(): (value or "").strip() for key, value in row.items()}
            if not any(source.values()):
                continue
            if any(len(value) > MAX_CELL_CHARS for value in source.values()):
                raise StatementImportError(f"Row {row_number} contains a cell longer than {MAX_CELL_CHARS} characters.")
            try:
                transaction_date = _parse_date(source.get(mapped["date"], ""))
                description = source.get(mapped["description"], "").strip()
                if not description:
                    raise StatementImportError("Description is required.")
                currency = _parse_currency(source.get(mapped.get("currency", ""), ""))
                if split_amounts:
                    debit_minor, debit_currency = _parse_money(source.get(mapped.get("debit", ""), ""))
                    credit_minor, credit_currency = _parse_money(source.get(mapped.get("credit", ""), ""))
                    if debit_minor < 0 or credit_minor < 0:
                        raise StatementImportError("Debit and credit columns must contain non-negative values.")
                    if debit_minor and credit_minor:
                        raise StatementImportError("A row cannot contain both a debit and a credit.")
                    amount_minor = credit_minor - debit_minor
                    amount_currencies = {item for item in (debit_currency, credit_currency) if item}
                    if len(amount_currencies) > 1:
                        raise StatementImportError("A row contains multiple currencies.")
                    inferred = next(iter(amount_currencies), None)
                else:
                    amount_minor, inferred = _parse_money(source.get(mapped["amount"], ""))
                    direction = _direction(source.get(mapped["direction"], ""))
                    if amount_minor < 0 and direction == "credit":
                        raise StatementImportError("Negative amount conflicts with a credit direction.")
                    if amount_minor > 0 and direction == "debit":
                        raise StatementImportError("Positive amount conflicts with a debit direction.")
                    amount_minor = abs(amount_minor) * (1 if direction == "credit" else -1)
                    debit_minor = max(0, -amount_minor)
                    credit_minor = max(0, amount_minor)
                    amount_currencies = {inferred} if inferred else set()
                if amount_minor == 0:
                    raise StatementImportError("Transaction amount must be non-zero.")
                if currency and amount_currencies and currency not in amount_currencies:
                    raise StatementImportError("Currency column conflicts with the amount symbol.")
                transaction_currency = currency or next(iter(amount_currencies), None)
                if transaction_currency:
                    currencies.add(transaction_currency)
                balance_minor = None
                if "balance" in mapped and source.get(mapped["balance"], ""):
                    balance_minor, balance_currency = _parse_money(source[mapped["balance"]])
                    if balance_currency:
                        if transaction_currency and balance_currency != transaction_currency:
                            raise StatementImportError("Running balance currency conflicts with the transaction currency.")
                        currencies.add(balance_currency)
                rows.append({
                    "row_number": row_number,
                    "date": transaction_date,
                    "description": description,
                    "debit_minor": debit_minor,
                    "credit_minor": credit_minor,
                    "amount_minor": amount_minor,
                    "balance_minor": balance_minor,
                    "currency": transaction_currency,
                    "source_values": source,
                })
            except StatementImportError as exc:
                raise StatementImportError(f"Row {row_number}: {exc}") from exc
        if not rows:
            raise StatementImportError("CSV contains no transaction rows.")
        if len(currencies) > 1:
            raise StatementImportError("Statement rows contain more than one currency.")
    except csv.Error as exc:
        raise StatementImportError("CSV quoting or row structure is invalid.") from exc

    for row in rows:
        row["currency"] = next(iter(currencies), "UNSPECIFIED")

    duplicate_rows = defaultdict(list)
    for row in rows:
        description_key = " ".join(row["description"].casefold().split())
        duplicate_rows[(row["date"], row["amount_minor"], description_key)].append(row["row_number"])
    row_by_number = {row["row_number"]: row for row in rows}
    duplicates = []
    for key, source_rows in duplicate_rows.items():
        if len(source_rows) > 1:
            duplicates.append({
                "rows": source_rows,
                "date": key[0],
                "amount_minor": key[1],
                "description": row_by_number[source_rows[0]]["description"],
            })

    has_balance_column = "balance" in mapped
    all_balances = has_balance_column and all(row["balance_minor"] is not None for row in rows)
    balance_issues = []
    opening = closing = None
    if all_balances:
        opening = rows[0]["balance_minor"] - rows[0]["amount_minor"]
        if abs(opening) > MAX_MINOR_UNITS:
            raise StatementImportError("Implied opening balance exceeds the supported exact numeric range.")
        previous = rows[0]["balance_minor"]
        for row in rows[1:]:
            expected = previous + row["amount_minor"]
            observed = row["balance_minor"]
            if abs(expected) > MAX_MINOR_UNITS or abs(observed - expected) > MAX_MINOR_UNITS:
                raise StatementImportError(f"Row {row['row_number']}: reconciliation exceeds the supported exact numeric range.")
            if expected != observed:
                balance_issues.append({
                    "row_number": row["row_number"],
                    "expected_minor": expected,
                    "observed_minor": observed,
                    "difference_minor": observed - expected,
                })
            previous = observed
        closing = rows[-1]["balance_minor"]
    balance_check = "unavailable" if not all_balances else ("inconsistent" if balance_issues else "consistent")
    currency = next(iter(currencies), "UNSPECIFIED")
    return {
        "transactions": rows,
        "transaction_count": len(rows),
        "currency": currency,
        "statement_analysis": {
            "currency": currency,
            "transaction_count": len(rows),
            "opening_balance_minor": opening,
            "closing_balance_minor": closing,
            "balance_check": balance_check,
            "balance_issues": balance_issues,
            "duplicate_candidates": duplicates,
            "balance_note": (
                "Running balances reconcile across the imported rows."
                if balance_check == "consistent" else
                "One or more running balances differ from the prior balance plus transaction amount."
                if balance_check == "inconsistent" else
                "A complete running balance column was not provided; balance reconciliation was unavailable."
            ),
            "extraction_status": "completed",
        },
    }


def parse_statement_pdf(pdf_document):
    """Extract supported digital statement tables from a PyMuPDF document.

    The extractor requires a recognizable header row and parseable transaction
    cells. It never uses OCR or guesses column meaning. Each extracted row keeps
    its page and a copy of the table row as evidence.
    """
    headers = None
    header_fields = None
    mapped_headers = None
    date_index = None
    table_rows = []
    row_evidence = []
    seen_tables = set()
    seen_rows = set()
    header_found = False
    extraction_error = None

    for page_number, page in enumerate(pdf_document, start=1):
        accepted_table_on_page = False
        for strategy in ("lines", "text"):
            try:
                tables = page.find_tables(strategy=strategy).tables
            except Exception:
                continue
            for table in tables:
                try:
                    cells = table.extract()
                except Exception:
                    continue
                if not cells:
                    continue
                identity = (page_number, tuple(getattr(table, "bbox", ())), repr(cells[:2]))
                if identity in seen_tables:
                    continue
                seen_tables.add(identity)
                cells = [["" if value is None else str(value).strip() for value in row] for row in cells]

                first_row = cells[0]
                if is_statement_header(first_row):
                    accepted_table_on_page = True
                    field_order = [_ALIAS_LOOKUP.get(_normal_header(value)) for value in first_row]
                    if header_fields is not None and field_order != header_fields:
                        extraction_error = "Statement table columns changed between pages."
                        continue
                    if headers is None:
                        headers = first_row
                        header_fields = field_order
                    mapped = _map_headers(first_row)
                    if mapped_headers is None:
                        mapped_headers = mapped
                    date_index = first_row.index(mapped["date"])
                    header_found = True
                    data_rows = cells[1:]
                elif headers is not None and date_index is not None and date_index < len(first_row):
                    data_rows = cells
                    accepted_table_on_page = True
                else:
                    continue

                for row in data_rows:
                    if date_index >= len(row):
                        continue
                    source_date = row[date_index].strip()
                    if not re.match(r"^(?:\d{1,4}[-/.]|\d{1,2}\s+[A-Za-z]{3}\s+)", source_date):
                        continue
                    try:
                        _parse_date(source_date)
                    except StatementImportError as exc:
                        extraction_error = str(exc)
                        continue
                    if len(row) != len(headers):
                        extraction_error = "A table row did not match the detected statement columns."
                        continue
                    row = list(row)
                    for field in ("debit", "credit", "amount", "balance"):
                        column = mapped_headers.get(field)
                        if column is not None:
                            column_index = headers.index(column)
                            if row[column_index] in {"-", "—", "–"}:
                                row[column_index] = ""
                    row_identity = (page_number, tuple(row))
                    if row_identity in seen_rows:
                        continue
                    seen_rows.add(row_identity)
                    table_rows.append(row)
                    row_evidence.append({
                        "page": page_number,
                        "evidence": " | ".join(value for value in row if value),
                    })
            if accepted_table_on_page:
                break

    currency = "UNSPECIFIED"
    if not header_found or not table_rows or extraction_error:
        note = (
            f"Statement table extraction needs review: {extraction_error}"
            if extraction_error else
            "No supported transaction table was detected. Scanned PDFs and unrecognized table layouts need manual review."
        )
        return {
            "transactions": [], "transaction_count": 0, "currency": currency,
            "statement_analysis": {
                "currency": currency, "transaction_count": 0,
                "opening_balance_minor": None, "closing_balance_minor": None,
                "balance_check": "unavailable", "balance_issues": [],
                "duplicate_candidates": [], "balance_note": note,
                "extraction_status": "needs_review",
            },
        }

    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(table_rows)
    try:
        imported = parse_statement_csv(stream.getvalue().encode("utf-8"))
    except StatementImportError as exc:
        return {
            "transactions": [], "transaction_count": 0, "currency": currency,
            "statement_analysis": {
                "currency": currency, "transaction_count": 0,
                "opening_balance_minor": None, "closing_balance_minor": None,
                "balance_check": "unavailable", "balance_issues": [],
                "duplicate_candidates": [],
                "balance_note": f"Statement table extraction needs review: {exc}",
                "extraction_status": "needs_review",
            },
        }
    for transaction, evidence in zip(imported["transactions"], row_evidence, strict=True):
        transaction.update(evidence)
    imported["statement_analysis"]["extraction_status"] = "completed"
    return imported


def _empty_ocr_statement(note):
    return {
        "transactions": [], "transaction_count": 0, "currency": "UNSPECIFIED",
        "statement_analysis": {
            "currency": "UNSPECIFIED", "transaction_count": 0,
            "opening_balance_minor": None, "closing_balance_minor": None,
            "balance_check": "unavailable", "balance_issues": [],
            "duplicate_candidates": [], "balance_note": note,
            "extraction_status": "needs_review",
        },
    }


def _ocr_word_lines(words):
    if not isinstance(words, list) or len(words) > 30_000:
        raise StatementImportError("OCR statement page exceeded its word limit.")
    valid_words = []
    for word in words:
        if not isinstance(word, dict):
            continue
        text = str(word.get("text", "")).strip()[:256]
        bbox = word.get("bbox")
        if not text or not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
            continue
        try:
            x0, y0, x1, y1 = (float(value) for value in bbox)
        except (TypeError, ValueError):
            continue
        if not all(map(math.isfinite, (x0, y0, x1, y1))) or x1 <= x0 or y1 <= y0:
            continue
        if min(x0, y0) < 0 or max(x1, y1) > 10_000:
            continue
        valid_words.append({"text": text, "bbox": (x0, y0, x1, y1)})
    valid_words.sort(key=lambda item: ((item["bbox"][1] + item["bbox"][3]) / 2, item["bbox"][0]))
    lines = []
    for word in valid_words:
        center_y = (word["bbox"][1] + word["bbox"][3]) / 2
        if lines:
            previous = lines[-1]
            previous_center = sum((item["bbox"][1] + item["bbox"][3]) / 2 for item in previous) / len(previous)
        else:
            previous_center = None
        if previous_center is not None and center_y - previous_center <= 4.5:
            previous.append(word)
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda item: item["bbox"][0])
    return lines


def _ocr_header_fields(line):
    matches = []
    cursor = 0
    while cursor < len(line):
        found = None
        for end in range(min(len(line), cursor + 4), cursor, -1):
            phrase = " ".join(item["text"] for item in line[cursor:end])
            field = _ALIAS_LOOKUP.get(_normal_header(phrase))
            if field == "direction" and end - cursor > 1:
                continue
            if field:
                found = (end, field)
                break
        if found:
            end, field = found
            selected = line[cursor:end]
            matches.append({
                "field": field,
                "center": (selected[0]["bbox"][0] + selected[-1]["bbox"][2]) / 2,
            })
            cursor = end
        else:
            cursor += 1
    fields = [item["field"] for item in sorted(matches, key=lambda item: item["center"])]
    if len(fields) != len(set(fields)) or not is_statement_header(fields):
        return None
    return sorted(matches, key=lambda item: item["center"])


def _normalize_ocr_cell(value, field):
    value = " ".join(value.split()).strip()
    if field == "date":
        return re.sub(r"\s*([/.-])\s*", r"\1", value).replace(".", "/")
    if field in {"debit", "credit", "amount", "balance"}:
        value = re.sub(r"\s*([,.])\s*", r"\1", value)
    return value


def parse_statement_ocr_pages(pages):
    """Extract conservative statement rows from OCR words with page coordinates.

    This path deliberately marks all OCR-derived imports for human review. It
    accepts only rows under recognized headers and routes every value through
    the same strict date, amount, currency, and reconciliation checks as CSV.
    """
    if not isinstance(pages, list) or not pages or len(pages) > 20:
        return _empty_ocr_statement("OCR statement extraction needs review: no supported OCR pages were supplied.")
    csv_headers = None
    extracted_rows = []
    source_evidence = []
    common_fields = None
    total_words = 0
    for page_item in pages:
        page_number = page_item.get("page") if isinstance(page_item, dict) else None
        try:
            page_number = int(page_number)
        except (TypeError, ValueError):
            return _empty_ocr_statement("OCR statement extraction needs review: a source page number was invalid.")
        try:
            page_words = page_item.get("words", [])
            total_words += len(page_words) if isinstance(page_words, list) else 0
            if total_words > 50_000:
                raise StatementImportError("OCR statement exceeded the document word limit.")
            lines = _ocr_word_lines(page_words)
        except StatementImportError as exc:
            return _empty_ocr_statement(f"OCR statement extraction needs review: {exc}")
        header_index = None
        columns = None
        for index, line in enumerate(lines):
            candidate = _ocr_header_fields(line)
            if candidate:
                header_index, columns = index, candidate
                break
        if columns is None:
            return _empty_ocr_statement(
                f"OCR statement extraction needs review: no recognized transaction header was found on page {page_number}."
            )
        fields = [column["field"] for column in columns]
        if common_fields is None:
            common_fields = fields
            csv_headers = fields
        elif fields != common_fields:
            return _empty_ocr_statement("OCR statement extraction needs review: transaction columns changed between pages.")
        centers = [column["center"] for column in columns]
        boundaries = [(left + right) / 2 for left, right in zip(centers, centers[1:])]
        date_rows_on_page = 0
        for line in lines[header_index + 1:]:
            repeated_header = _ocr_header_fields(line)
            if repeated_header:
                repeated_fields = [column["field"] for column in repeated_header]
                repeated_centers = [column["center"] for column in repeated_header]
                if (repeated_fields != fields or len(repeated_centers) != len(centers)
                        or any(abs(left - right) > 15 for left, right in zip(centers, repeated_centers, strict=True))):
                    return _empty_ocr_statement(
                        f"OCR statement extraction needs review: table columns changed on page {page_number}."
                    )
                continue
            cells = [[] for _ in columns]
            for word in line:
                x0, _y0, x1, _y1 = word["bbox"]
                column_index = bisect_right(boundaries, (x0 + x1) / 2)
                cells[column_index].append(word["text"])
            values = {
                column["field"]: _normalize_ocr_cell(" ".join(cell), column["field"])
                for column, cell in zip(columns, cells, strict=True)
            }
            date_value = values.get("date", "")
            if not date_value:
                non_description = any(value for key, value in values.items() if key != "description")
                description = values.get("description", "")
                summary_line = any(
                    phrase in description.casefold()
                    for phrase in ("opening balance", "closing balance", "brought forward", "carried forward", "statement total")
                )
                if summary_line:
                    continue
                if (description and not non_description and extracted_rows
                        and source_evidence[-1]["page"] == page_number):
                    previous = extracted_rows[-1]
                    previous["description"] = f"{previous['description']} {description}".strip()
                    previous_evidence = source_evidence[-1]
                    previous_evidence["text"] = f"{previous_evidence['text']} {description}".strip()
                    previous_evidence["bbox"] = [
                        min(previous_evidence["bbox"][0], min(word["bbox"][0] for word in line)),
                        min(previous_evidence["bbox"][1], min(word["bbox"][1] for word in line)),
                        max(previous_evidence["bbox"][2], max(word["bbox"][2] for word in line)),
                        max(previous_evidence["bbox"][3], max(word["bbox"][3] for word in line)),
                    ]
                elif non_description:
                    return _empty_ocr_statement(
                        f"OCR statement extraction needs review: a row on page {page_number} has values but no readable date."
                    )
                continue
            try:
                _parse_date(date_value)
            except StatementImportError:
                return _empty_ocr_statement(
                    f"OCR statement extraction needs review: an OCR date on page {page_number} could not be normalized."
                )
            if not values.get("description"):
                return _empty_ocr_statement(
                    f"OCR statement extraction needs review: a dated row on page {page_number} has no readable description."
                )
            extracted_rows.append(values)
            source_evidence.append({
                "page": page_number,
                "text": " ".join(word["text"] for word in line),
                "bbox": [
                    min(word["bbox"][0] for word in line), min(word["bbox"][1] for word in line),
                    max(word["bbox"][2] for word in line), max(word["bbox"][3] for word in line),
                ],
            })
            date_rows_on_page += 1
        if date_rows_on_page == 0:
            return _empty_ocr_statement(
                f"OCR statement extraction needs review: no dated transactions were found on page {page_number}."
            )

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=csv_headers)
    writer.writeheader()
    writer.writerows(extracted_rows)
    try:
        imported = parse_statement_csv(stream.getvalue().encode("utf-8"))
    except StatementImportError as exc:
        return _empty_ocr_statement(f"OCR statement extraction needs review: {exc}")
    for row, evidence in zip(imported["transactions"], source_evidence, strict=True):
        row.update({
            "page": evidence["page"], "evidence": evidence["text"],
            "bbox": evidence["bbox"], "extraction_method": "pymupdf_tesseract_ocr",
        })
    imported["statement_analysis"]["extraction_status"] = "needs_review"
    imported["statement_analysis"]["balance_note"] += " OCR-derived transaction cells require human verification against the original scan."
    return imported
