"""Generate a small, deterministic, fictional corpus with independent PDF layouts."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "runtime" / "ml" / "layout-variants-v1"
SYNTHETIC_MARK = "SYNTHETIC DOCUMENT - FICTIONAL DATA - FOR SOFTWARE TESTING ONLY"
BASE_VARIANTS = (
    {"family": "layout-form-v1", "amount": 525000, "rate": 12.2, "months": 36,
     "fee": "INR 5,250", "late_fee": "2.00%", "kind": "fixed"},
    {"family": "layout-table-v2", "amount": 725000, "rate": 11.4, "months": 48,
     "fee": "0.75%", "late_fee": "1.50%", "kind": "floating"},
    {"family": "layout-narrative-v3", "amount": 385000, "rate": 15.1, "months": 24,
     "fee": "INR 3,850", "late_fee": "INR 350", "kind": "fixed"},
)
LAYOUT_STYLES = ("form", "table", "narrative")
FIELD_LABELS = (
    {"amount": "Loan Amount", "rate": "Rate of Interest", "emi": "Equated Monthly Installment", "fee": "Processing Fee", "late": "Late payment charge"},
    {"amount": "Sanctioned Amount", "rate": "Interest Rate", "emi": "Monthly Instalment", "fee": "Processing Fee", "late": "Penal charge"},
    {"amount": "Principal Amount", "rate": "Annual Interest Rate", "emi": "EMI", "fee": "Processing Fee", "late": "Overdue charge"},
    {"amount": "Amount of Loan", "rate": "Annual Interest Rate", "emi": "Monthly Installment", "fee": "Processing Fee", "late": "Late payment penalty"},
    {"amount": "Loan Amount", "rate": "Interest Rate", "emi": "EMI", "fee": "Processing Fee", "late": "Penal charge"},
    {"amount": "Principal Amount", "rate": "Rate of Interest", "emi": "Equated Monthly Instalment", "fee": "Processing Fee", "late": "Overdue charge"},
)
DOC_TYPES = ("loan_agreement", "sanction_letter", "repayment_schedule")


def inr(value: int) -> str:
    number = str(value)
    head, tail = number[:-3], number[-3:]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(groups + [tail]) if groups else tail


def emi(principal: int, annual_rate: float, months: int) -> int:
    monthly_rate = annual_rate / 1200
    if monthly_rate == 0:
        return round(principal / months)
    return round(principal * monthly_rate / (1 - (1 + monthly_rate) ** -months))


def _variant(index: int) -> dict:
    base = dict(BASE_VARIANTS[index]) if index < len(BASE_VARIANTS) else {
        "amount": 250000 + (index * 7919) % 1_750_000,
        "rate": round(8.5 + (index * 0.37) % 9, 2),
        "months": (12, 24, 36, 48, 60, 72, 84, 120, 180, 240)[index % 10],
        "fee": (f"INR {inr(2500 + index * 137)}" if index % 2 == 0 else f"{0.50 + (index % 6) * 0.25:.2f}%"),
        "late_fee": (f"{1.00 + (index % 5) * 0.25:.2f}%" if index % 2 == 0 else f"INR {250 + index * 5}"),
        "kind": "floating" if index % 3 == 0 else "fixed",
    }
    style = LAYOUT_STYLES[index % len(LAYOUT_STYLES)]
    return {
        **base,
        "family": f"layout-{style}-{index + 1:03}",
        "layout_style": style,
        "labels": dict(FIELD_LABELS[index % len(FIELD_LABELS)]),
        "order_seed": index,
    }


def _lines(doc_type: str, record: dict) -> list[str]:
    kind = record["kind"].title()
    amount = f"INR {inr(record['amount'])}"
    rate = f"{record['rate']:.2f}%"
    term = f"{record['months']} months"
    installment = f"INR {emi(record['amount'], record['rate'], record['months']):,}"
    labels = record["labels"]
    term_lines = [
        f"{labels['amount']}: {amount}",
        f"{labels['rate']}: {rate} per annum ({kind})",
        f"Tenure: {term}",
        f"{labels['emi']}: {installment}",
        f"{labels['fee']}: {record['fee']}",
        f"{labels['late']}: {record['late_fee']}",
        f"Rate type: {kind}",
    ]
    random.Random(record["order_seed"]).shuffle(term_lines)
    title = {
        "loan_agreement": "PERSONAL LOAN AGREEMENT",
        "sanction_letter": "PERSONAL LOAN SANCTION LETTER",
        "repayment_schedule": "PERSONAL LOAN REPAYMENT SCHEDULE",
    }[doc_type]
    if record["layout_style"] == "form":
        return [
            title, SYNTHETIC_MARK, f"Reference Number: {record['id']}",
            "Product: Personal Loan", "", "KEY TERMS", *term_lines,
            "", "The borrower shall repay this fictional loan in monthly installments.",
        ]
    if record["layout_style"] == "table":
        return [
            title, SYNTHETIC_MARK, f"Reference Number: {record['id']}",
            "PRODUCT: Personal Loan", "", "FINANCIAL SUMMARY",
            "FIELD                                      VALUE",
            *term_lines,
            "", "Repayment is due on the date shown in the schedule.",
        ]
    return [
        title, SYNTHETIC_MARK, f"Reference Number {record['id']}",
        "Facility: Personal Loan", "", "TERMS OF THE FACILITY",
        f"The {labels['amount'].lower()} under this instrument is {amount}.",
        f"The {labels['rate'].lower()} applicable to this facility is {rate}.",
        f"Rate type: {kind}.",
        f"The repayment tenure is {term}.",
        f"{labels['emi']} due each month: {installment}.",
        f"{labels['fee']} payable: {record['fee']}.",
        f"{labels['late']}: {record['late_fee']}.",
        "The borrower will repay according to the agreed monthly schedule.",
    ]


def _make_pdf(path: Path, lines: list[str], table_style: bool) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    pdf.setTitle("Synthetic banking document")
    y = 800
    for index, line in enumerate(lines):
        if index == 0:
            pdf.setFont("Helvetica-Bold", 13)
            pdf.drawCentredString(A4[0] / 2, y, line)
            y -= 28
            continue
        if table_style and index >= 6 and index <= 13:
            pdf.setStrokeColorRGB(0.75, 0.78, 0.80)
            pdf.line(48, y - 4, A4[0] - 48, y - 4)
            pdf.setFont("Courier", 8.5)
        else:
            pdf.setFont("Helvetica-Bold" if line.isupper() else "Helvetica", 9.5)
        pdf.drawString(50, y, line)
        y -= 22 if table_style and index >= 6 and index <= 13 else 16
    pdf.setFont("Helvetica", 8)
    pdf.drawString(50, 34, "FICTIONAL DOCUMENT | SYNTHETIC TEST DATA")
    pdf.showPage()
    pdf.save()


def build(output: Path = DEFAULT_OUTPUT, family_count: int = len(BASE_VARIANTS)) -> dict:
    if family_count < 3:
        raise ValueError("At least three layout families are required for train/validation/test splits.")
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for variant_index in range(1, family_count + 1):
        variant = _variant(variant_index - 1)
        for doc_type in DOC_TYPES:
            short = {"loan_agreement": "AG", "sanction_letter": "SL", "repayment_schedule": "RS"}[doc_type]
            doc_id = f"SYN-LAYOUT-{variant_index}-{short}"
            record = {**variant, "id": doc_id}
            lines = _lines(doc_type, record)
            relative = Path("documents") / f"{doc_id.lower()}.pdf"
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            _make_pdf(path, lines, variant["layout_style"] == "table")
            text_pages = []
            with pymupdf.open(path) as document:
                text_pages = [page.get_text() for page in document]
            raw_amount = f"INR {inr(variant['amount'])}"
            raw_rate = f"{variant['rate']:.2f}"
            raw_term = f"{variant['months']} months"
            raw_emi = f"INR {emi(variant['amount'], variant['rate'], variant['months']):,}"
            expected_terms = {
                "loan_amount": {"value": float(variant["amount"]), "status": "extracted", "page": 1, "raw": raw_amount},
                "interest_rate": {"value": float(variant["rate"]), "status": "extracted", "page": 1, "raw": raw_rate},
                "interest_type": {"value": variant["kind"], "status": "extracted", "page": 1, "raw": variant["kind"]},
                "tenure": {"value": variant["months"], "status": "extracted", "page": 1, "raw": raw_term},
                "emi": {"value": float(emi(variant["amount"], variant["rate"], variant["months"])), "status": "extracted", "page": 1, "raw": raw_emi},
                "processing_fee": {"value": variant["fee"], "status": "extracted", "page": 1, "raw": variant["fee"]},
                "late_fee": {"value": variant["late_fee"], "status": "extracted", "page": 1, "raw": variant["late_fee"]},
            }
            entries.append({
                "id": doc_id,
                "file": relative.as_posix(),
                "expected_http": 201,
                "doc_type": doc_type,
                "loan_category": "personal",
                "page_count": len(text_pages),
                "expected_terms": expected_terms,
                "synthetic": True,
                "source_family": variant["family"],
                "review_state": "synthetic_ground_truth_not_human_reviewed",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            })

    manifest = {
        "generator": "scripts/make_layout_variants.py",
        "dataset_id": "bankease-layout-variants-v1",
        "note": "All documents are fictional; labels are generated from construction and have not been human reviewed.",
        "files": entries,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--family-count", type=int, default=len(BASE_VARIANTS))
    args = parser.parse_args()
    manifest = build(args.output, args.family_count)
    print(f"Generated {len(manifest['files'])} synthetic PDFs across {args.family_count} layout families at {args.output}")
    print("Labels are construction-derived, not human-reviewed; use for regression and split testing only.")


if __name__ == "__main__":
    main()
