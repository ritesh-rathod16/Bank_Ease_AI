"""Evaluate deterministic extraction on the repository's synthetic regression fixtures."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import extract  # noqa: E402
from prepare_ml_dataset import DEFAULT_MANIFEST, prepare  # noqa: E402


def _same_value(expected, actual) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(expected), float(actual), rel_tol=0, abs_tol=0.01)
    return expected == actual


def _rate(correct: int, total: int) -> dict:
    return {"correct": correct, "total": total, "rate": round(correct / total, 6) if total else None}


def evaluate(manifest_path: Path = DEFAULT_MANIFEST, split: str | None = None) -> dict:
    # Preparation validates checksums, the fictional-data marker, and source paths.
    prepared_documents, _, corpus_manifest = prepare(manifest_path)
    by_id = {row["document_id"]: row for row in prepared_documents}
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [entry for entry in source_manifest["files"] if entry.get("expected_http") == 201]
    if split is not None:
        if split not in {"train", "validation", "test"}:
            raise ValueError("split must be train, validation, or test")
        entries = [entry for entry in entries if by_id[entry["id"]]["split"] == split]
        if not entries:
            raise ValueError(f"The prepared corpus has no documents in the {split!r} split.")
    classifications = {"doc_type": [0, 0], "loan_category": [0, 0]}
    field_metrics: dict[str, dict[str, int]] = {}
    errors = []
    partial_docs = []
    evaluated_field_docs = 0

    for entry in entries:
        prepared = by_id[entry["id"]]
        source = manifest_path.parent / entry["file"]
        with pymupdf.open(source) as pdf:
            pages = [page.get_text() for page in pdf]
        predicted_type, predicted_category = extract.classify(pages)
        for key, expected, actual in (
            ("doc_type", entry["doc_type"], predicted_type),
            ("loan_category", entry.get("loan_category"), predicted_category),
        ):
            classifications[key][1] += 1
            if expected == actual:
                classifications[key][0] += 1
            else:
                errors.append({"document_id": entry["id"], "task": key,
                               "expected": expected, "actual": actual})

        # This baseline intentionally measures selectable text only; OCR is evaluated separately.
        if any(len(page.strip()) < 20 for page in pages):
            partial_docs.append(entry["id"])
            continue
        evaluated_field_docs += 1
        predictions = extract.extract_terms(pages)
        for name, expected in sorted(entry.get("expected_terms", {}).items()):
            actual = predictions.get(name, {})
            metrics = field_metrics.setdefault(name, {
                "value_correct": 0, "value_total": 0,
                "status_correct": 0, "status_total": 0,
                "page_correct": 0, "page_total": 0,
                "evidence_supported": 0, "evidence_total": 0,
            })
            expected_value = expected.get("value")
            actual_value = actual.get("value")
            metrics["value_total"] += 1
            metrics["status_total"] += 1
            if _same_value(expected_value, actual_value):
                metrics["value_correct"] += 1
            if expected.get("status") == actual.get("status"):
                metrics["status_correct"] += 1
            expected_page = expected.get("page")
            if expected_page is not None:
                metrics["page_total"] += 1
                if actual.get("page") == expected_page:
                    metrics["page_correct"] += 1
            if expected.get("raw") is not None:
                metrics["evidence_total"] += 1
                if (actual.get("evidence") and actual.get("page")
                        and 1 <= actual["page"] <= len(pages)
                        and actual["evidence"] in pages[actual["page"] - 1]):
                    metrics["evidence_supported"] += 1
            checks = {
                "value": _same_value(expected_value, actual_value),
                "status": expected.get("status") == actual.get("status"),
                "page": expected_page is None or expected_page == actual.get("page"),
            }
            if not all(checks.values()) and len(errors) < 200:
                errors.append({"document_id": entry["id"], "task": "field", "field": name,
                               "checks": checks,
                               "expected": {"value": expected_value, "status": expected.get("status"), "page": expected_page},
                               "actual": {"value": actual_value, "status": actual.get("status"), "page": actual.get("page")}})

    scope = (
        f"Synthetic {split} split grouped by source/layout family; generated labels are not human-reviewed, "
        "and this is not a real-world accuracy estimate."
        if split else
        "Synthetic regression fixtures only; all known templates are included, so this is not held-out evaluation and is not a real-world accuracy estimate."
    )
    return {
        "evaluation_id": "bankease-deterministic-synthetic-regression-v1",
        "scope": scope,
        "split": split,
        "source_families": sorted({by_id[entry["id"]]["source_family"] for entry in entries}),
        "model": "deterministic rules in backend/app/extract.py",
        "ocr_used": False,
        "manifest_sha256": corpus_manifest["provenance"]["manifest_sha256"],
        "counts": {
            "classification_documents": len(entries),
            "field_documents": evaluated_field_docs,
            "field_documents_excluded_for_unreadable_text": sorted(partial_docs),
        },
        "classification": {
            key: _rate(correct, total) for key, (correct, total) in classifications.items()
        },
        "field_metrics": {
            name: {
                "value_exact_or_tolerance": _rate(values["value_correct"], values["value_total"]),
                "status_exact": _rate(values["status_correct"], values["status_total"]),
                "page_exact": _rate(values["page_correct"], values["page_total"]),
                "evidence_supported": _rate(values["evidence_supported"], values["evidence_total"]),
            }
            for name, values in sorted(field_metrics.items())
        },
        "error_examples": errors,
        "training_or_model_changes": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--split", choices=("train", "validation", "test"))
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "ml" / "synthetic-baseline-evaluation.json")
    args = parser.parse_args()
    report = evaluate(args.manifest, args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Evaluated {report['counts']['classification_documents']} synthetic documents.")
    print(f"Document-type rule match: {report['classification']['doc_type']['correct']}/{report['classification']['doc_type']['total']}")
    print(f"Field evaluation excluded {len(report['counts']['field_documents_excluded_for_unreadable_text'])} partially unreadable document(s).")
    print(f"Report: {args.output}")
    if report["split"]:
        print("This is a held-out synthetic-family regression report, not a real-world accuracy claim.")
    else:
        print("This is a synthetic regression report, not a held-out or real-world accuracy claim.")


if __name__ == "__main__":
    main()
