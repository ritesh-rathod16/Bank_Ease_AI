"""Validate FinQA split provenance and emit normalized JSONL without split leakage."""
import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")


def report_family(example_id: str) -> str:
    value = re.sub(r"-\d+$", "", example_id.strip())
    parts = value.split("/")
    if len(parts) < 3:
        return parts[0]
    # FinQA IDs include company/year/page; grouping at company/year keeps sibling report pages together.
    return "/".join(parts[:2])


def load_split(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a list of examples in {path.name}.")
    return data


def build(input_dir: Path, output_dir: Path):
    manifest_path = input_dir / "manifest.json"
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("dataset_id") != "finqa" or source_manifest.get("license") is None:
        raise ValueError("A provenance-bearing FinQA acquisition manifest is required.")
    for file_info in source_manifest.get("files", []):
        path = input_dir / file_info["name"]
        if not path.is_file() or path.stat().st_size != file_info["bytes"]:
            raise ValueError(f"Missing or size-mismatched source file: {path.name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != file_info["sha256"]:
            raise ValueError(f"Source checksum mismatch: {path.name}")

    rows_by_family = defaultdict(list)
    upstream_family_splits = defaultdict(set)
    seen_ids = {}
    for split in SPLITS:
        path = input_dir / f"{split}.json"
        if not path.exists():
            raise ValueError(f"Missing required split: {path.name}")
        for source in load_split(path):
            example_id = str(source.get("id", "")).strip()
            qa = source.get("qa") or {}
            if not example_id or not isinstance(qa, dict) or not qa.get("question"):
                raise ValueError(f"Malformed FinQA item in {path.name}: missing id or question")
            identity = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            if example_id in seen_ids:
                if seen_ids[example_id] != identity:
                    raise ValueError(f"Conflicting duplicate example id: {example_id}")
                continue
            seen_ids[example_id] = identity
            family = report_family(example_id)
            upstream_family_splits[family].add(split)
            rows_by_family[family].append({
                "id": example_id,
                "report_family": family,
                "upstream_split": split,
                "question": str(qa["question"]).strip(),
                "answer": qa.get("exe_ans"),
                "program": qa.get("program"),
                "evidence": qa.get("gold_inds", {}),
                "pre_text": source.get("pre_text", []),
                "table": source.get("table", []),
                "post_text": source.get("post_text", []),
                "source_dataset": "finqa",
                "source_url": source_manifest["source_url"],
                "license": source_manifest["license"],
                "synthetic": False,
            })

    # The upstream FinQA split has company/year families in more than one split.
    # Repartition whole families using a stable hash order and keep the original
    # split label as provenance; do not leak report siblings into evaluation.
    family_order = sorted(
        rows_by_family,
        key=lambda family: hashlib.sha256(f"bankease-finqa-v1:{family}".encode("utf-8")).hexdigest(),
    )
    family_count = len(family_order)
    if family_count < 3:
        raise ValueError("At least three report families are required for family-disjoint train/dev/test splits.")
    train_end = max(1, min(family_count - 2, round(family_count * 0.8)))
    dev_end = max(train_end + 1, min(family_count - 1, round(family_count * 0.9)))
    rows_by_split = {split: [] for split in SPLITS}
    for index, family in enumerate(family_order):
        split = "train" if index < train_end else "dev" if index < dev_end else "test"
        for row in rows_by_family[family]:
            row["split"] = split
            rows_by_split[split].append(row)

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for split, rows in rows_by_split.items():
        target = output_dir / f"{split}.jsonl"
        target.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        outputs.append({"file": target.name, "rows": len(rows),
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    prepared = {
        "dataset_id": "bankease-finqa-v1",
        "source_manifest": str(manifest_path),
        "source_revision": source_manifest.get("version"),
        "license": source_manifest["license"],
        "preprocessing": "Validate checksums; deduplicate exact IDs; group by company/year report family; deterministically repartition whole families 80/10/10; retain original upstream split as provenance.",
        "split_method": "Sort report families by SHA-256(bankease-finqa-v1:<family>); assign the first 80% to train, next 10% to dev, remainder to test.",
        "upstream_family_cross_split_count": sum(len(splits) > 1 for splits in upstream_family_splits.values()),
        "split_families": {split: len({row["report_family"] for row in rows}) for split, rows in rows_by_split.items()},
        "files": outputs,
        "limitations": source_manifest.get("limitations", []),
        "training_approved": False,
        "note": "Human review, source rights review, and a task-specific evaluation gate are required before any training run.",
    }
    (output_dir / "manifest.json").write_text(json.dumps(prepared, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/runtime/ml/public/finqa-v1")
    parser.add_argument("--output", type=Path, default=ROOT / "data/runtime/ml/prepared/finqa-v1")
    args = parser.parse_args()
    try:
        result = build(args.input, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"dataset_id": result["dataset_id"], "files": result["files"],
                      "training_approved": result["training_approved"]}, indent=2))


if __name__ == "__main__":
    main()
