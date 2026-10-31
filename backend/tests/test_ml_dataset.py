import json
import shutil
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_ml_dataset import (  # noqa: E402
    DEFAULT_MANIFEST,
    DatasetPreparationError,
    prepare,
    write_dataset,
)
from evaluate_synthetic_baseline import evaluate  # noqa: E402
from make_layout_variants import build as build_layout_variants  # noqa: E402
from train_document_classifier import (  # noqa: E402
    TrainingGateError,
    classification_metrics,
    validate_corpus,
)
from prepare_chat_sft_dataset import (  # noqa: E402
    DatasetError as ChatDatasetError,
    prepare as prepare_chat_sft,
    write_dataset as write_chat_sft,
)
from train_chat_adapter import (  # noqa: E402
    TrainingError as ChatTrainingError,
    _sample_rows,
    load_corpus as load_chat_corpus,
)
import train_chat_adapter  # noqa: E402
from evaluate_chat_adapter import score_answer as score_chat_answer  # noqa: E402


def test_synthetic_corpus_is_reproducible_and_grouped_by_template_family(tmp_path):
    documents, fields, manifest = prepare(DEFAULT_MANIFEST, seed=4815)
    again_documents, again_fields, again_manifest = prepare(DEFAULT_MANIFEST, seed=4815)

    assert documents == again_documents
    assert fields == again_fields
    assert manifest == again_manifest
    assert len(documents) == 20
    assert len(fields) == 20 * 7
    assert manifest["evaluation_readiness"]["source_family_count"] == 3
    assert manifest["evaluation_readiness"]["training_ready"] is False
    assert manifest["evaluation_readiness"]["held_out_class_coverage"] is False
    assert set(manifest["split_by_source_family"].values()) == {"train", "validation", "test"}
    by_family = {}
    for row in documents:
        assert row["source"]["synthetic"] is True
        by_family.setdefault(row["source_family"], set()).add(row["split"])
    assert all(len(splits) == 1 for splits in by_family.values())

    field = next(row for row in fields if row["label"]["field_name"] == "loan_amount")
    evidence = field["label"]["evidence"]
    assert evidence["status"] == "matched"
    sample = DEFAULT_MANIFEST.parent / field["source"]["path"]
    import pymupdf
    with pymupdf.open(sample) as pdf:
        page = pdf[evidence["page"] - 1].get_text()
    assert page[evidence["start"]:evidence["end"]] == field["label"]["raw_value"]

    out_a, out_b = tmp_path / "a", tmp_path / "b"
    write_dataset(out_a, documents, fields, manifest)
    write_dataset(out_b, again_documents, again_fields, again_manifest)
    for name in ("documents.jsonl", "fields.jsonl", "split_manifest.json"):
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes()


def test_preparation_rejects_bad_checksums_and_external_paths(tmp_path):
    entry = json.loads(DEFAULT_MANIFEST.read_text())["files"][0]
    sample = DEFAULT_MANIFEST.parent / entry["file"]
    root = tmp_path / "dataset"
    nested = root / "personal"
    nested.mkdir(parents=True)
    copied = nested / sample.name
    shutil.copyfile(sample, copied)
    entry["file"] = "personal/" + sample.name
    entry["sha256"] = "0" * 64
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps({"generator": "scripts/make_datasets.py", "files": [entry]}))
    with pytest.raises(DatasetPreparationError, match="Checksum mismatch"):
        prepare(manifest_path)

    entry["sha256"] = __import__("hashlib").sha256(copied.read_bytes()).hexdigest()
    outside = tmp_path / "outside.pdf"
    shutil.copyfile(sample, outside)
    entry["file"] = "../outside.pdf"
    manifest_path.write_text(json.dumps({"generator": "scripts/make_datasets.py", "files": [entry]}))
    with pytest.raises(DatasetPreparationError, match="escapes"):
        prepare(manifest_path)


def test_preparation_refuses_unmarked_data(tmp_path):
    entry = json.loads(DEFAULT_MANIFEST.read_text())["files"][0]
    sample = DEFAULT_MANIFEST.parent / entry["file"]
    root = tmp_path / "dataset"
    root.mkdir()
    entry["file"] = sample.name
    entry["synthetic"] = False
    copied = root / sample.name
    shutil.copyfile(sample, copied)
    (root / "manifest.json").write_text(json.dumps({"generator": "scripts/make_datasets.py", "files": [entry]}))
    with pytest.raises(DatasetPreparationError, match="non-synthetic"):
        prepare(root / "manifest.json")


def test_deterministic_baseline_report_labels_its_synthetic_scope():
    report = evaluate()
    assert report["counts"]["classification_documents"] == 20
    assert report["counts"]["field_documents"] == 19
    assert report["ocr_used"] is False
    assert report["training_or_model_changes"] is False
    assert "not held-out evaluation" in report["scope"]
    assert report["classification"]["doc_type"]["total"] == 20
    assert all(0 <= metric["value_exact_or_tolerance"]["rate"] <= 1
               for metric in report["field_metrics"].values())


def test_layout_variants_split_by_family_with_all_document_types_in_each_split(tmp_path):
    first = build_layout_variants(tmp_path / "first")
    second = build_layout_variants(tmp_path / "second")
    assert first == second
    for entry_a, entry_b in zip(first["files"], second["files"], strict=True):
        assert entry_a["sha256"] == entry_b["sha256"]
        assert (tmp_path / "first" / entry_a["file"]).read_bytes() == (
            tmp_path / "second" / entry_b["file"]
        ).read_bytes()

    manifest_path = tmp_path / "first" / "manifest.json"
    documents, fields, manifest = prepare(manifest_path)
    assert len(documents) == 9
    assert len(fields) == 63
    assert manifest["evaluation_readiness"]["training_ready"] is False
    assert manifest["evaluation_readiness"]["held_out_class_coverage"] is True
    assert "labels_are_synthetic_or_not_human_reviewed" in manifest["evaluation_readiness"]["blockers"]
    assert "insufficient_examples_per_class_per_split" in manifest["evaluation_readiness"]["blockers"]
    assert set(manifest["split_by_source_family"].values()) == {"train", "validation", "test"}
    family_splits = {}
    for row in documents:
        family_splits.setdefault(row["source_family"], set()).add(row["split"])
    assert all(len(splits) == 1 for splits in family_splits.values())
    for split in ("train", "validation", "test"):
        assert set(manifest["counts"]["classes"][split]) == {
            "loan_agreement", "sanction_letter", "repayment_schedule",
        }
        assert manifest["counts"]["documents"][split] == 3
        assert manifest["counts"]["field_labels"][split] == 21

    report = evaluate(manifest_path, split="test")
    assert report["counts"]["classification_documents"] == 3
    assert report["classification"]["doc_type"] == {"correct": 3, "total": 3, "rate": 1.0}
    assert report["classification"]["loan_category"] == {"correct": 3, "total": 3, "rate": 1.0}
    assert report["error_examples"] == []
    for metrics in report["field_metrics"].values():
        assert metrics["value_exact_or_tolerance"]["rate"] == 1.0
        assert metrics["status_exact"]["rate"] == 1.0
        assert metrics["page_exact"]["rate"] == 1.0
        assert metrics["evidence_supported"]["rate"] == 1.0
    assert "not a real-world accuracy estimate" in report["scope"]


def test_classifier_training_requires_explicit_synthetic_gate_and_uses_family_splits(tmp_path):
    root = tmp_path / "corpus"
    source_manifest = build_layout_variants(root, family_count=67)
    prepared = root / "prepared"
    documents, fields, split_manifest = prepare(root / "manifest.json")
    write_dataset(prepared, documents, fields, split_manifest)

    with pytest.raises(TrainingGateError, match="--allow-synthetic-research"):
        validate_corpus(prepared, root / "manifest.json")

    rows, labels, validated_manifest, _source = validate_corpus(
        prepared, root / "manifest.json", allow_synthetic_research=True,
    )
    assert len(rows) == len(source_manifest["files"]) == 201
    assert labels == ["loan_agreement", "repayment_schedule", "sanction_letter"]
    assert validated_manifest["evaluation_readiness"]["blockers"] == [
        "labels_are_synthetic_or_not_human_reviewed",
    ]
    assert validated_manifest["counts"]["documents"] == {"train": 141, "validation": 30, "test": 30}


def test_classifier_metrics_report_per_class_and_confusion_matrix():
    metrics = classification_metrics([0, 0, 1, 1, 2], [0, 1, 1, 2, 2], ["a", "b", "c"])
    assert metrics["correct"] == 3
    assert metrics["accuracy"] == 0.6
    assert metrics["confusion_matrix"]["counts"] == [[1, 1, 0], [0, 1, 1], [0, 0, 1]]
    assert metrics["per_class"]["a"]["recall"] == 0.5


def test_chat_sft_corpus_has_citations_abstention_and_family_disjoint_splits(tmp_path):
    root = tmp_path / "variants"
    build_layout_variants(root, family_count=12)
    documents, fields, split_manifest = prepare(root / "manifest.json")
    prepared = root / "prepared"
    write_dataset(prepared, documents, fields, split_manifest)

    rows, manifest = prepare_chat_sft(prepared)
    all_families = {}
    for split, examples in rows.items():
        assert examples
        for example in examples:
            metadata = example["metadata"]
            all_families.setdefault(metadata["source_family"], set()).add(split)
            assert metadata["synthetic"] is True
            answer = example["messages"][2]["content"]
            if metadata["field_name"]:
                assert "[D1, page 1]" in answer
                assert metadata["expected_value"]
            else:
                assert "can't confirm" in answer
                assert metadata["expected_value"] is None
    assert all(len(splits) == 1 for splits in all_families.values())
    assert manifest["training_approved"] is False
    assert manifest["customer_data_included"] is False

    output = tmp_path / "chat-sft"
    write_chat_sft(output, rows, manifest)
    loaded_manifest, loaded_rows = load_chat_corpus(output, allow_synthetic_research=True)
    assert loaded_manifest["dataset_id"] == manifest["dataset_id"]
    assert sum(map(len, loaded_rows.values())) == sum(map(len, rows.values()))
    with pytest.raises(ChatTrainingError, match="not training-approved"):
        load_chat_corpus(output, allow_synthetic_research=False)


def test_chat_sft_refuses_corrupt_source_checksum_and_checksum_tampering(tmp_path):
    root = tmp_path / "variants"
    build_layout_variants(root, family_count=12)
    documents, fields, split_manifest = prepare(root / "manifest.json")
    prepared = root / "prepared"
    write_dataset(prepared, documents, fields, split_manifest)
    rows, manifest = prepare_chat_sft(prepared)
    output = tmp_path / "chat-sft"
    write_chat_sft(output, rows, manifest)
    train_path = output / "train.jsonl"
    train_path.write_text(train_path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    with pytest.raises(ChatTrainingError, match="Checksum mismatch"):
        load_chat_corpus(output, allow_synthetic_research=True)

    source_row = json.loads((prepared / "documents.jsonl").read_text(encoding="utf-8").splitlines()[0])
    source_row["source"]["synthetic"] = False
    (prepared / "documents.jsonl").write_text(
        json.dumps(source_row) + "\n", encoding="utf-8"
    )
    with pytest.raises(ChatDatasetError):
        prepare_chat_sft(prepared)


def test_chat_training_smoke_subset_is_reproducibly_sampled():
    rows = [{"id": index} for index in range(100)]
    selected = _sample_rows(rows, 12, seed=2026)
    assert selected == _sample_rows(rows, 12, seed=2026)
    assert len(selected) == 12
    assert selected != rows[:12]
    assert _sample_rows(rows, 0, seed=2026) == rows


def test_chat_training_cli_reports_resource_gate_without_traceback(monkeypatch, capsys):
    def blocked(_args):
        raise ChatTrainingError("Only 1.7 GiB RAM is available; need 3.5 GiB.")

    monkeypatch.setattr(train_chat_adapter, "train", blocked)
    monkeypatch.setattr(sys, "argv", ["train_chat_adapter.py", "--allow-synthetic-research"])
    with pytest.raises(SystemExit) as error:
        train_chat_adapter.main()

    assert error.value.code == 2
    assert "Training blocked: Only 1.7 GiB RAM is available" in capsys.readouterr().err


def test_chat_adapter_evaluator_checks_exact_value_page_and_abstention():
    supported = score_chat_answer("INR 12,500", 3, "The document states INR 12,500 [D1, page 3].")
    assert supported == {
        "field_value_exact_substring": True,
        "page_citation_present": True,
        "abstention_phrase_present": None,
    }
    wrong_page = score_chat_answer("INR 12,500", 3, "INR 12,500 [D1, page 2].")
    assert wrong_page["field_value_exact_substring"] is True
    assert wrong_page["page_citation_present"] is False
    abstention = score_chat_answer(None, 1, "I cannot confirm this from the provided excerpt.")
    assert abstention["abstention_phrase_present"] is True
