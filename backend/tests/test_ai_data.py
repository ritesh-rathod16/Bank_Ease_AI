import hashlib
import json

import pytest

from scripts.prepare_finqa import build


def make_source(root, name, example_id, answer="7"):
    row = [{"id": example_id, "pre_text": ["Revenue grew."], "table": [["Year", "2024"]],
            "post_text": ["End of report."], "qa": {"question": "What value is stated?", "exe_ans": answer,
                                                        "program": ["7"], "gold_inds": {"Revenue": "7"}}}]
    path = root / f"{name}.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    return {"name": path.name, "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_finqa_preparation_checksums_and_repartitions_report_families(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    files = [
        make_source(source, "train", "AAA/2018/page_1.pdf-0"),
        make_source(source, "dev", "BBB/2019/page_2.pdf-0"),
        make_source(source, "test", "CCC/2020/page_3.pdf-0"),
    ]
    manifest = {"dataset_id": "finqa", "source_url": "https://example.test/finqa",
                "version": "a" * 40, "license": "CC BY 4.0", "files": files, "limitations": []}
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    output = tmp_path / "prepared"
    result = build(source, output)
    assert result["training_approved"] is False
    assert result["split_families"] == {"train": 1, "dev": 1, "test": 1}
    prepared_rows = [json.loads(line) for split in ("train", "dev", "test")
                     for line in (output / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()]
    family_splits = {row["report_family"]: row["split"] for row in prepared_rows}
    assert set(family_splits) == {"AAA/2018", "BBB/2019", "CCC/2020"}
    assert set(family_splits.values()) == {"train", "dev", "test"}
    prepared = next(row for row in prepared_rows if row["report_family"] == "AAA/2018")
    assert prepared["synthetic"] is False
    assert prepared["upstream_split"] in {"train", "dev", "test"}

    dev_path = source / "dev.json"
    upstream_dev = json.loads(dev_path.read_text(encoding="utf-8"))
    upstream_dev.append({"id": "AAA/2018/page_9.pdf-1", "qa": {"question": "Test?", "exe_ans": 1}})
    dev_path.write_text(json.dumps(upstream_dev), encoding="utf-8")
    manifest["files"][1] = {"name": dev_path.name, "bytes": dev_path.stat().st_size,
                            "sha256": hashlib.sha256(dev_path.read_bytes()).hexdigest()}
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    repartitioned = build(source, tmp_path / "repartitioned")
    assert repartitioned["upstream_family_cross_split_count"] == 1
    moved_rows = [json.loads(line) for split in ("train", "dev", "test")
                  for line in (tmp_path / "repartitioned" / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()]
    aaa_splits = {row["split"] for row in moved_rows if row["report_family"] == "AAA/2018"}
    assert len(aaa_splits) == 1


def test_acquisition_requires_explicit_license_review(monkeypatch):
    from scripts import acquire_public_dataset

    monkeypatch.setattr("sys.argv", ["acquire_public_dataset.py"])
    with pytest.raises(SystemExit) as error:
        acquire_public_dataset.main()
    assert error.value.code == 2


def test_acquisition_reports_network_block_without_traceback(monkeypatch, capsys):
    from urllib.error import URLError
    from scripts import acquire_public_dataset

    monkeypatch.setattr("sys.argv", ["acquire_public_dataset.py", "--accept-license-review"])
    monkeypatch.setattr(acquire_public_dataset, "fetch_json", lambda _url: (_ for _ in ()).throw(URLError("blocked socket")))
    with pytest.raises(SystemExit) as error:
        acquire_public_dataset.main()
    assert error.value.code == 2
    assert "Could not acquire FinQA from GitHub" in capsys.readouterr().err
