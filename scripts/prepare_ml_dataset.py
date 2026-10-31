"""Prepare a reproducible synthetic-only corpus; this script does not train a model."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "samples" / "manifest.json"
PREPROCESSING_VERSION = "bankease-synthetic-corpus-v1"
SPLIT_NAMES = ("train", "validation", "test")
MIN_DOCUMENTS_PER_CLASS = {"train": 20, "validation": 5, "test": 10}
FAMILY_BY_TYPE = {
    "loan_agreement": "synthetic-loan-agreement-layout-v1",
    "sanction_letter": "synthetic-sanction-letter-layout-v1",
    "repayment_schedule": "synthetic-repayment-schedule-layout-v1",
}


class DatasetPreparationError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _evidence(text: str, raw: str | None) -> dict:
    """Return a page-local source line and offsets only when raw is actually present."""
    if not raw:
        return {"text": None, "start": None, "end": None, "status": "not_required"}
    needle = _normalized(raw)
    offset = 0
    for line in text.splitlines(keepends=True):
        visible = line.rstrip("\r\n")
        folded = visible.casefold()
        start = folded.find(raw.casefold())
        if start >= 0:
            return {"text": visible.strip(), "start": offset + start,
                    "end": offset + start + len(raw), "status": "matched"}
        if needle and needle in _normalized(visible):
            # Whitespace-normalized evidence is useful as context, but offsets are
            # omitted because they would not point to the original byte/codepoint span.
            return {"text": visible.strip(), "start": None, "end": None, "status": "matched_context_only"}
        offset += len(line)
    return {"text": None, "start": None, "end": None, "status": "unavailable"}


def _split_groups(group_sizes: dict[str, int], seed: int) -> dict[str, str]:
    """Assign entire template families to splits, balancing document counts deterministically."""
    total = sum(group_sizes.values())
    targets = {"train": total * 0.70, "validation": total * 0.15, "test": total * 0.15}
    counts = {name: 0 for name in SPLIT_NAMES}
    assignments: dict[str, str] = {}
    ordered = sorted(
        group_sizes,
        key=lambda group: (-group_sizes[group], hashlib.sha256(f"{seed}:{group}".encode()).hexdigest()),
    )
    for group in ordered:
        size = group_sizes[group]
        def score(split: str) -> tuple[float, int]:
            # Place each intact family in the currently least-filled split. The
            # previous squared-target cost could prefer train repeatedly when
            # there were only a few equal-sized groups, leaving test empty.
            return counts[split] / max(targets[split], 1), SPLIT_NAMES.index(split)
        selected = min(SPLIT_NAMES, key=score)
        assignments[group] = selected
        counts[selected] += size
    return assignments


def prepare(manifest_path: Path, seed: int = 2026) -> tuple[list[dict], list[dict], dict]:
    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent.resolve(strict=True)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    allowed_generators = {"scripts/make_datasets.py", "scripts/make_layout_variants.py"}
    if manifest.get("generator") not in allowed_generators:
        raise DatasetPreparationError("Only a repository synthetic dataset manifest is accepted.")

    accepted = [entry for entry in manifest.get("files", []) if entry.get("expected_http") == 201]
    if not accepted:
        raise DatasetPreparationError("The manifest has no accepted synthetic documents.")

    pending_documents = []
    group_sizes: dict[str, int] = {}
    labels_seen: set[str] = set()
    evidence_missing = []
    for entry in accepted:
        if entry.get("synthetic") is not True:
            raise DatasetPreparationError(f"Refusing non-synthetic sample: {entry.get('id', '<unknown>')}")
        doc_type = entry.get("doc_type")
        family = entry.get("source_family") or FAMILY_BY_TYPE.get(doc_type)
        if family is None:
            raise DatasetPreparationError(f"No source-family mapping is defined for {doc_type!r}.")
        relative = Path(entry["file"])
        source = (root / relative).resolve(strict=True)
        if not source.is_relative_to(root) or not source.is_file():
            raise DatasetPreparationError(f"Sample path escapes the dataset directory: {entry['file']}")
        content = source.read_bytes()
        digest = sha256_bytes(content)
        if digest != entry.get("sha256"):
            raise DatasetPreparationError(f"Checksum mismatch for {entry['id']}.")
        if source.suffix.casefold() != ".pdf" or not content.startswith(b"%PDF"):
            raise DatasetPreparationError(f"Expected a valid synthetic PDF for {entry['id']}.")
        try:
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                pages = [page.get_text() for page in pdf]
        except Exception as exc:
            raise DatasetPreparationError(f"Could not parse synthetic PDF {entry['id']}.") from exc
        if len(pages) != entry.get("page_count"):
            raise DatasetPreparationError(f"Page count mismatch for {entry['id']}.")
        if not pages or "SYNTHETIC" not in pages[0]:
            raise DatasetPreparationError(f"Synthetic marker missing from {entry['id']}.")
        pending_documents.append((entry, family, pages, digest))
        group_sizes[family] = group_sizes.get(family, 0) + 1
        labels_seen.add(doc_type)

    split_by_family = _split_groups(group_sizes, seed)
    documents, fields = [], []
    classes_by_split = {name: set() for name in SPLIT_NAMES}
    for entry, family, pages, digest in pending_documents:
        split = split_by_family[family]
        classes_by_split[split].add(entry["doc_type"])
        text = "\n\n".join(pages)
        documents.append({
            "document_id": entry["id"], "source_family": family, "split": split,
            "text": text, "label": {"doc_type": entry["doc_type"], "loan_category": entry.get("loan_category")},
            "source": {"path": entry["file"], "sha256": digest, "synthetic": True,
                       "generator": manifest["generator"],
                       "review_state": entry.get("review_state", "synthetic_ground_truth_not_human_reviewed")},
        })
        for name, expected in sorted(entry.get("expected_terms", {}).items()):
            page_number = expected.get("page")
            page_text = pages[page_number - 1] if isinstance(page_number, int) and 1 <= page_number <= len(pages) else ""
            evidence = _evidence(page_text, expected.get("raw"))
            if expected.get("raw") and evidence["status"] == "unavailable":
                evidence_missing.append({"document_id": entry["id"], "field": name, "page": page_number})
            fields.append({
                "document_id": entry["id"], "source_family": family, "split": split,
                "text": text,
                "label": {
                    "field_name": name, "normalized_value": expected.get("value"),
                    "status": expected.get("status"), "raw_value": expected.get("raw"),
                    "page": page_number,
                    "evidence": {**evidence, "page": page_number},
                },
                "source": {"path": entry["file"], "sha256": digest, "synthetic": True,
                           "generator": manifest["generator"],
                           "review_state": entry.get("review_state", "synthetic_ground_truth_not_human_reviewed")},
            })

    all_classes = set(labels_seen)
    missing_coverage = {
        split: sorted(all_classes - classes_by_split[split])
        for split in SPLIT_NAMES
    }
    held_out_coverage = not any(missing_coverage.values())
    labels_human_reviewed = all(
        entry.get("review_state") == "human_reviewed"
        for entry, _family, _pages, _digest in pending_documents
    )
    class_document_counts = {
        split: {
            label: sum(1 for row in documents if row["split"] == split and row["label"]["doc_type"] == label)
            for label in sorted(all_classes)
        }
        for split in SPLIT_NAMES
    }
    training_blockers = []
    if not held_out_coverage:
        training_blockers.append("missing_class_coverage_in_one_or_more_splits")
    if len(group_sizes) < 3:
        training_blockers.append("fewer_than_three_independent_source_families")
    insufficient_examples = {
        split: {label: count for label, count in counts.items()
                if count < MIN_DOCUMENTS_PER_CLASS[split]}
        for split, counts in class_document_counts.items()
    }
    insufficient_examples = {split: counts for split, counts in insufficient_examples.items() if counts}
    if insufficient_examples:
        training_blockers.append("insufficient_examples_per_class_per_split")
    if not labels_human_reviewed:
        training_blockers.append("labels_are_synthetic_or_not_human_reviewed")
    trainable = not training_blockers
    split_manifest = {
        "dataset_id": "bankease-synthetic-financial-docs-v1",
        "preprocessing_version": PREPROCESSING_VERSION,
        "seed": seed,
        "provenance": {
            "source": manifest["generator"],
            "manifest_sha256": sha256_bytes(manifest_bytes),
            "license": "Not applicable; repository-generated fictional data.",
            "permitted_use": "Development and evaluation only; not representative of real banking documents.",
        },
        "split_method": "Deterministic whole-template-family assignment; related layouts never cross splits.",
        "split_by_source_family": split_by_family,
        "annotation_status": "human_reviewed" if labels_human_reviewed else "synthetic_ground_truth_not_human_reviewed",
        "counts": {
            "documents": {split: sum(1 for row in documents if row["split"] == split) for split in SPLIT_NAMES},
            "field_labels": {split: sum(1 for row in fields if row["split"] == split) for split in SPLIT_NAMES},
            "classes": {split: sorted(classes_by_split[split]) for split in SPLIT_NAMES},
            "documents_by_class": class_document_counts,
        },
        "evaluation_readiness": {
            "training_ready": trainable,
            "held_out_class_coverage": held_out_coverage,
            "missing_class_coverage": missing_coverage,
            "source_family_count": len(group_sizes),
            "minimum_documents_per_class_by_split": MIN_DOCUMENTS_PER_CLASS,
            "class_counts_below_minimum": insufficient_examples,
            "blockers": training_blockers,
            "reason": None if trainable else "; ".join(training_blockers),
        },
        "evidence_quality": {
            "raw_value_labels_without_matching_source_line": evidence_missing,
            "matched_source_line_count": sum(row["label"]["evidence"]["status"].startswith("matched") for row in fields),
        },
    }
    documents.sort(key=lambda row: row["document_id"])
    fields.sort(key=lambda row: (row["document_id"], row["label"]["field_name"]))
    return documents, fields, split_manifest


def _jsonl(rows: list[dict]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def write_dataset(output: Path, documents: list[dict], fields: list[dict], split_manifest: dict) -> None:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    files = {
        "documents.jsonl": _jsonl(documents),
        "fields.jsonl": _jsonl(fields),
        "split_manifest.json": (json.dumps(split_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    }
    for name, content in files.items():
        fd, temporary = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=output)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output / name)
        finally:
            Path(temporary).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "ml" / "synthetic-v1")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    documents, fields, split_manifest = prepare(args.manifest, args.seed)
    write_dataset(args.output, documents, fields, split_manifest)
    readiness = split_manifest["evaluation_readiness"]
    print(f"Prepared {len(documents)} synthetic documents and {len(fields)} field labels at {args.output}")
    print(f"Source families: {readiness['source_family_count']}; training-ready: {readiness['training_ready']}")
    if not readiness["training_ready"]:
        print("No model training is warranted from this corpus; see split_manifest.json for coverage limits.")


if __name__ == "__main__":
    main()
