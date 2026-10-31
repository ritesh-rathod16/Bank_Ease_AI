"""Fine-tune a local pretrained document classifier for a controlled experiment.

The default data gate requires human-reviewed labels. Synthetic development data
can be used only with --allow-synthetic-research and is never presented as a
production model or real-world evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "runtime" / "ml" / "gpt2-classifier-v1" / "prepared"
DEFAULT_SOURCE_MANIFEST = ROOT / "data" / "runtime" / "ml" / "gpt2-classifier-v1" / "manifest.json"
DEFAULT_OUTPUT = ROOT / "data" / "runtime" / "ml" / "gpt2-classifier-v1" / "model"
ALLOWED_GENERATORS = {"scripts/make_datasets.py", "scripts/make_layout_variants.py"}
MIN_DOCUMENTS_PER_CLASS = {"train": 20, "validation": 5, "test": 10}
EXPECTED_TYPES = {"loan_agreement", "sanction_letter", "repayment_schedule"}


class TrainingGateError(ValueError):
    """Raised when the requested training corpus fails provenance/split checks."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingGateError(f"Could not read valid JSONL from {path}.") from exc


def validate_corpus(dataset_dir: Path, source_manifest_path: Path,
                    allow_synthetic_research: bool = False) -> tuple[list[dict], list[str], dict, dict]:
    """Validate held-out family splits, provenance, checksums, and explicit data gate."""
    dataset_dir = dataset_dir.resolve(strict=True)
    manifest_path = (dataset_dir / "split_manifest.json").resolve(strict=True)
    if not manifest_path.is_relative_to(dataset_dir):
        raise TrainingGateError("The split manifest must stay inside the prepared dataset directory.")
    source_manifest_path = source_manifest_path.resolve(strict=True)
    split_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_bytes = source_manifest_path.read_bytes()
    source_manifest = json.loads(source_bytes)
    if source_manifest.get("generator") not in ALLOWED_GENERATORS:
        raise TrainingGateError("The source manifest is not from an approved repository corpus generator.")
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    if source_digest != split_manifest.get("provenance", {}).get("manifest_sha256"):
        raise TrainingGateError("Source manifest checksum does not match the prepared split manifest.")

    documents_path = (dataset_dir / "documents.jsonl").resolve(strict=True)
    if not documents_path.is_relative_to(dataset_dir):
        raise TrainingGateError("The document JSONL must stay inside the prepared dataset directory.")
    documents = _read_jsonl(documents_path)
    if not documents:
        raise TrainingGateError("The training corpus contains no documents.")
    files = {entry.get("id"): entry for entry in source_manifest.get("files", [])
             if entry.get("expected_http") == 201}
    rows_by_id = {row.get("document_id"): row for row in documents}
    if len(rows_by_id) != len(documents) or set(rows_by_id) != set(files):
        raise TrainingGateError("Prepared documents do not match the source manifest IDs exactly.")

    family_splits = split_manifest.get("split_by_source_family", {})
    for row in documents:
        source = row.get("source", {})
        entry = files[row["document_id"]]
        if source.get("synthetic") is not True or entry.get("synthetic") is not True:
            raise TrainingGateError(f"Refusing non-synthetic source document {row['document_id']}.")
        if row.get("label", {}).get("doc_type") != entry.get("doc_type"):
            raise TrainingGateError(f"Label mismatch for source document {row['document_id']}.")
        family = row.get("source_family")
        if not family or family_splits.get(family) != row.get("split"):
            raise TrainingGateError(f"Source-family split mismatch for {row['document_id']}.")
        source_path = (source_manifest_path.parent / entry["file"]).resolve(strict=True)
        if not source_path.is_relative_to(source_manifest_path.parent.resolve(strict=True)):
            raise TrainingGateError(f"Source path escapes dataset directory for {row['document_id']}.")
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if digest != entry.get("sha256") or digest != source.get("sha256"):
            raise TrainingGateError(f"Source checksum mismatch for {row['document_id']}.")

    classes = sorted({row.get("label", {}).get("doc_type") for row in documents})
    if set(classes) != EXPECTED_TYPES:
        raise TrainingGateError(f"Expected the three supported document classes; found {classes}.")
    families_per_split: dict[str, set[str]] = {name: set() for name in ("train", "validation", "test")}
    for row in documents:
        split = row.get("split")
        if split not in families_per_split:
            raise TrainingGateError(f"Unsupported split {split!r}.")
        families_per_split[split].add(row["source_family"])
    if not all(families_per_split.values()) or any(
        families_per_split[a] & families_per_split[b]
        for a, b in (("train", "validation"), ("train", "test"), ("validation", "test"))
    ):
        raise TrainingGateError("Train, validation, and test must each contain disjoint source families.")

    counts = {
        split: {label: sum(1 for row in documents if row.get("split") == split and row.get("label", {}).get("doc_type") == label)
                for label in classes}
        for split in ("train", "validation", "test")
    }
    insufficient = {split: {label: count for label, count in label_counts.items()
                            if count < MIN_DOCUMENTS_PER_CLASS[split]}
                    for split, label_counts in counts.items()}
    insufficient = {split: values for split, values in insufficient.items() if values}
    if insufficient:
        raise TrainingGateError(f"Insufficient examples per class/split: {insufficient}.")
    readiness = split_manifest.get("evaluation_readiness", {})
    if not readiness.get("held_out_class_coverage") or readiness.get("missing_class_coverage") != {
        "train": [], "validation": [], "test": [],
    }:
        raise TrainingGateError("Prepared manifest does not confirm class coverage in all held-out splits.")
    blockers = readiness.get("blockers", [])
    non_review_blockers = [item for item in blockers if item != "labels_are_synthetic_or_not_human_reviewed"]
    if non_review_blockers:
        raise TrainingGateError(f"Dataset readiness blockers remain: {non_review_blockers}.")
    if blockers and not allow_synthetic_research:
        raise TrainingGateError(
            "Labels are synthetic/unreviewed. Pass --allow-synthetic-research to run an explicitly non-production experiment."
        )
    if split_manifest.get("annotation_status") != "human_reviewed" and not allow_synthetic_research:
        raise TrainingGateError("Human-reviewed labels are required unless --allow-synthetic-research is set.")
    return documents, classes, split_manifest, source_manifest


def classification_metrics(expected: list[int], predicted: list[int], labels: list[str]) -> dict:
    confusion = [[0 for _ in labels] for _ in labels]
    for truth, guess in zip(expected, predicted, strict=True):
        confusion[truth][guess] += 1
    per_class = {}
    f1_values = []
    for index, label in enumerate(labels):
        tp = confusion[index][index]
        fp = sum(confusion[other][index] for other in range(len(labels)) if other != index)
        fn = sum(confusion[index][other] for other in range(len(labels)) if other != index)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6),
                            "support": sum(confusion[index])}
        f1_values.append(f1)
    correct = sum(confusion[index][index] for index in range(len(labels)))
    return {
        "accuracy": round(correct / len(expected), 6) if expected else None,
        "macro_f1": round(sum(f1_values) / len(f1_values), 6) if f1_values else None,
        "per_class": per_class,
        "confusion_matrix": {"labels": labels, "counts": confusion},
        "correct": correct,
        "total": len(expected),
    }


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def train(args: argparse.Namespace) -> dict:
    documents, classes, split_manifest, source_manifest = validate_corpus(
        args.dataset, args.manifest, args.allow_synthetic_research,
    )
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch
        import peft
        from peft import LoraConfig, TaskType, get_peft_model
        import transformers
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("Install the pinned optional dependencies from requirements-ml.txt.") from exc

    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError(f"Refusing to overwrite a non-empty model artifact directory: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    seed = args.seed
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(max(1, min(args.cpu_threads, os.cpu_count() or 1)))
    try:
        torch.use_deterministic_algorithms(True, warn_only=True)
    except AttributeError:
        pass

    label_to_id = {label: index for index, label in enumerate(classes)}
    model_name = args.base_model
    print(f"Loading {model_name} from the local Hugging Face cache (offline mode).", flush=True)
    local_model_path = Path(snapshot_download(repo_id=model_name, local_files_only=True))
    tokenizer = AutoTokenizer.from_pretrained(local_model_path, local_files_only=True)
    if tokenizer.eos_token is None:
        raise RuntimeError("The tokenizer needs an EOS token to use as a padding token.")
    tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForSequenceClassification.from_pretrained(
        local_model_path,
        num_labels=len(classes),
        id2label={index: label for label, index in label_to_id.items()},
        label2id=label_to_id,
        local_files_only=True,
        pad_token_id=tokenizer.pad_token_id,
    )
    model.config.pad_token_id = tokenizer.pad_token_id
    model.config.problem_type = "single_label_classification"
    lora = LoraConfig(
        task_type=TaskType.SEQ_CLS,
        r=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=["c_attn", "c_proj"],
        modules_to_save=["score"],
        fan_in_fan_out=True,
    )
    model = get_peft_model(model, lora)
    model.to(torch.device("cpu"))
    trainable_params = sum(param.numel() for param in model.parameters() if param.requires_grad)
    total_params = sum(param.numel() for param in model.parameters())
    print(f"CPU LoRA run: {trainable_params:,}/{total_params:,} trainable parameters; "
          f"{len(documents)} synthetic docs, {len(classes)} classes.", flush=True)

    def encode(rows: list[dict]) -> tuple[dict[str, Any], Any, list[int]]:
        tokenized = tokenizer(
            [row["text"] for row in rows],
            truncation=True,
            max_length=args.max_length,
            padding="max_length",
            add_special_tokens=False,
            return_tensors="pt",
        )
        targets = [label_to_id[row["label"]["doc_type"]] for row in rows]
        return tokenized, torch.tensor(targets, dtype=torch.long), targets

    rows_by_split = {name: [row for row in documents if row["split"] == name]
                     for name in ("train", "validation", "test")}
    train_tokens, train_targets, _ = encode(rows_by_split["train"])
    validation_tokens, _validation_targets, validation_expected = encode(rows_by_split["validation"])
    test_tokens, _test_targets, test_expected = encode(rows_by_split["test"])
    optimizer = torch.optim.AdamW((param for param in model.parameters() if param.requires_grad),
                                  lr=args.learning_rate, weight_decay=args.weight_decay)
    best_validation = -1.0
    best_epoch = 0
    best_trainable_state = None
    epoch_history = []

    def predict(tokenized: dict[str, Any], batch_size: int) -> list[int]:
        output_ids = []
        model.eval()
        with torch.no_grad():
            for start in range(0, len(tokenized["input_ids"]), batch_size):
                batch = {key: value[start:start + batch_size] for key, value in tokenized.items()}
                logits = model(**batch).logits
                output_ids.extend(logits.argmax(dim=-1).cpu().tolist())
        return output_ids

    for epoch in range(1, args.epochs + 1):
        model.train()
        order = list(range(len(train_targets)))
        random.Random(seed + epoch).shuffle(order)
        losses = []
        for start in range(0, len(order), args.batch_size):
            indices = order[start:start + args.batch_size]
            batch = {key: value[indices] for key, value in train_tokens.items()}
            target_batch = train_targets[indices]
            optimizer.zero_grad(set_to_none=True)
            loss = model(**batch, labels=target_batch).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_((param for param in model.parameters() if param.requires_grad), 1.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        validation_prediction = predict(validation_tokens, args.batch_size)
        val_metrics = classification_metrics(validation_expected, validation_prediction, classes)
        epoch_history.append({"epoch": epoch, "mean_training_loss": round(sum(losses) / len(losses), 6),
                              "validation": val_metrics})
        print(f"Epoch {epoch}/{args.epochs}: loss={epoch_history[-1]['mean_training_loss']:.4f}, "
              f"validation accuracy={val_metrics['accuracy']:.3f}", flush=True)
        if val_metrics["accuracy"] >= best_validation:
            best_validation = val_metrics["accuracy"]
            best_epoch = epoch
            best_trainable_state = {
                name: param.detach().cpu().clone()
                for name, param in model.named_parameters() if param.requires_grad
            }
            model.save_pretrained(args.output)
            tokenizer.save_pretrained(args.output)

    if best_trainable_state is None:
        raise RuntimeError("Training finished without a validation checkpoint.")
    with torch.no_grad():
        for name, param in model.named_parameters():
            if param.requires_grad:
                param.copy_(best_trainable_state[name].to(param.device))

    test_predictions = predict(test_tokens, args.batch_size)
    test_metrics = classification_metrics(test_expected, test_predictions, classes)

    # Compare against the existing deterministic classifier over the same held-out PDFs.
    sys.path.insert(0, str(ROOT))
    from backend.app import extract  # noqa: PLC0415
    import pymupdf  # noqa: PLC0415
    entries = {entry["id"]: entry for entry in source_manifest["files"] if entry.get("expected_http") == 201}
    baseline_expected, baseline_predictions = [], []
    source_root = args.manifest.resolve(strict=True).parent.resolve(strict=True)
    for row in rows_by_split["test"]:
        entry = entries[row["document_id"]]
        pdf_path = (source_root / entry["file"]).resolve(strict=True)
        if not pdf_path.is_relative_to(source_root):
            raise TrainingGateError(f"Test PDF path escapes dataset root for {row['document_id']}.")
        with pymupdf.open(pdf_path) as pdf:
            pages = [page.get_text() for page in pdf]
        predicted_type, _category = extract.classify(pages)
        baseline_expected.append(label_to_id[row["label"]["doc_type"]])
        baseline_predictions.append(label_to_id.get(predicted_type, -1))
    baseline_metrics = classification_metrics(baseline_expected, baseline_predictions, classes)

    family_counts = {split: len({row["source_family"] for row in rows_by_split[split]})
                     for split in rows_by_split}
    layout_styles_by_split = {
        split: sorted({
            row["source_family"].split("-")[1]
            for row in rows_by_split[split]
            if row["source_family"].startswith("layout-")
            and len(row["source_family"].split("-")) >= 3
        })
        for split in rows_by_split
    }
    layout_style_overlap = any(
        layout_styles_by_split[left] and layout_styles_by_split[right]
        and set(layout_styles_by_split[left]) & set(layout_styles_by_split[right])
        for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))
    )
    artifact_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(args.output.iterdir())
        if path.is_file() and path.name != "bankease_model_card.json"
    }
    report = {
        "experiment_id": "bankease-gpt2-document-classifier-synthetic-lora-v1",
        "status": "completed_synthetic_research_experiment",
        "production_ready": False,
        "scope": "Synthetic construction-labeled documents only; not a real-world accuracy or generalization estimate.",
        "data": {
            "dataset_id": split_manifest.get("dataset_id"),
            "source_generator": source_manifest.get("generator"),
            "source_manifest_sha256": split_manifest["provenance"]["manifest_sha256"],
            "source_families_by_split": family_counts,
            "reused_layout_styles_by_split": layout_styles_by_split,
            "base_layout_styles_recur_across_splits": layout_style_overlap,
            "documents_by_split": {split: len(rows) for split, rows in rows_by_split.items()},
            "labels_human_reviewed": split_manifest.get("annotation_status") == "human_reviewed",
        },
        "base_model": model_name,
        "base_model_revision": getattr(model.config, "_commit_hash", None) or local_model_path.name,
        "runtime": {
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "peft": peft.__version__,
            "base_model_load_local_files_only": True,
        },
        "fine_tuning": {
            "method": "LoRA sequence classification",
            "rank": args.lora_rank,
            "alpha": args.lora_alpha,
            "dropout": args.lora_dropout,
            "target_modules": ["c_attn", "c_proj"],
            "epochs_requested": args.epochs,
            "best_validation_epoch": best_epoch,
            "checkpoint_selection": "highest validation accuracy; held-out test is scored from the saved best checkpoint",
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
            "max_length": args.max_length,
            "seed": seed,
            "device": "cpu",
            "trainable_parameters": trainable_params,
            "total_parameters": total_params,
            "epoch_history": epoch_history,
        },
        "classes": classes,
        "held_out_test": test_metrics,
        "deterministic_rule_baseline_same_test": baseline_metrics,
        "model_artifact_directory": str(args.output.resolve()),
        "model_artifact_sha256": artifact_hashes,
        "limitations": [
            "Generated labels describe construction, not human-verified document understanding.",
            "The corpus varies synthetic text, values, labels, and three layout styles; it does not represent real bank documents or OCR noise.",
            "The same three base layout styles recur across train, validation, and test; this is not an unseen-layout evaluation.",
            "Only document-type classification was fine-tuned; financial field extraction remains on the deterministic baseline.",
            "Do not use for customer, lending, compliance, or other production decisions.",
        ],
    }
    _write_json(args.report, report)
    card = {
        "model_name": report["experiment_id"],
        "base_model": model_name,
        "base_model_revision": report["base_model_revision"],
        "runtime": report["runtime"],
        "model_artifact_sha256": artifact_hashes,
        "task": "experimental synthetic banking-document type classification",
        "training_method": "PEFT LoRA; CPU; GPT-2 sequence-classification head",
        "dataset": report["data"],
        "evaluation": {"held_out_test": test_metrics, "deterministic_rule_baseline_same_test": baseline_metrics},
        "production_ready": False,
        "intended_use": "Research and software pipeline verification with fictional synthetic documents only.",
        "prohibited_use": "Do not use for real customer documents or financial decisions.",
        "limitations": report["limitations"],
    }
    _write_json(args.output / "bankease_model_card.json", card)
    print(f"Held-out test accuracy: {test_metrics['correct']}/{test_metrics['total']} "
          f"({test_metrics['accuracy']:.3f}); rule baseline: "
          f"{baseline_metrics['correct']}/{baseline_metrics['total']} ({baseline_metrics['accuracy']:.3f}).", flush=True)
    print(f"Adapter: {args.output.resolve()}\nExperiment report: {args.report.resolve()}", flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_OUTPUT.parent / "experiment.json")
    parser.add_argument("--base-model", default="gpt2")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=0.0002)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--lora-rank", type=int, default=4)
    parser.add_argument("--lora-alpha", type=int, default=8)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--allow-synthetic-research", action="store_true",
                        help="Explicitly permit a non-production experiment on synthetic, unreviewed labels.")
    args = parser.parse_args()
    if args.epochs < 1 or args.batch_size < 1 or args.max_length < 8 or args.lora_rank < 1:
        parser.error("epochs, batch size, LoRA rank, and max length must be positive (max length >= 8).")
    report = train(args)
    print(f"Completed experiment {report['experiment_id']} (production_ready=false).")


if __name__ == "__main__":
    main()
