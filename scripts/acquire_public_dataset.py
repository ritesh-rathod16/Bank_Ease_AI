"""Resumably acquire the reviewed, small FinQA JSON splits without executing dataset code."""
import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
COMMIT_URL = "https://api.github.com/repos/czyssrs/FinQA/commits/main"
CONTENTS_URL = "https://api.github.com/repos/czyssrs/FinQA/contents/dataset?ref={}"
ALLOWED_FILES = {"train.json", "dev.json", "test.json"}
MAX_DEFAULT_BYTES = 32 * 1024 * 1024


def fetch_json(url: str):
    request = Request(url, headers={"User-Agent": "BankEase-AI-public-dataset-preparer/1.0", "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def download_resumable(url: str, path: Path, expected_size: int, max_bytes: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > expected_size:
        partial.unlink()
        offset = 0
    headers = {"User-Agent": "BankEase-AI-public-dataset-preparer/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = Request(url, headers=headers)
    for attempt in range(4):
        try:
            with urlopen(request, timeout=30) as response:
                if offset and response.status != 206:
                    partial.unlink(missing_ok=True)
                    offset = 0
                mode = "ab" if offset else "wb"
                received = offset
                with partial.open(mode) as output:
                    while block := response.read(1024 * 1024):
                        received += len(block)
                        if received > expected_size or received > max_bytes:
                            raise ValueError(f"Download exceeded its declared or configured size limit: {path.name}")
                        output.write(block)
                if received != expected_size:
                    raise OSError(f"Incomplete download for {path.name}: {received}/{expected_size} bytes; rerun to resume")
            partial.replace(path)
            return
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if attempt == 3:
                raise RuntimeError(f"Could not finish {path.name}; partial data is retained for resume: {exc}") from exc
            time.sleep(1 + attempt)
            offset = partial.stat().st_size if partial.exists() else 0
            headers["Range"] = f"bytes={offset}-" if offset else ""
            if not headers["Range"]:
                headers.pop("Range")
            request = Request(url, headers=headers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/runtime/ml/public/finqa-v1")
    parser.add_argument("--max-bytes", type=int, default=MAX_DEFAULT_BYTES)
    parser.add_argument("--accept-license-review", action="store_true",
                        help="Acknowledge CC BY attribution and review of underlying source-report terms.")
    args = parser.parse_args()
    if not args.accept_license_review:
        parser.error("Review the FinQA license and underlying source-report terms, then pass --accept-license-review to acquire data.")
    if args.max_bytes <= 0:
        parser.error("--max-bytes must be positive")
    try:
        revision = fetch_json(COMMIT_URL).get("sha")
        if not isinstance(revision, str) or len(revision) != 40:
            parser.error("Could not resolve an immutable FinQA source revision.")
        entries = fetch_json(CONTENTS_URL.format(revision))
        selected = [item for item in entries if item.get("name") in ALLOWED_FILES]
        if {item.get("name") for item in selected} != ALLOWED_FILES:
            parser.error("The expected FinQA train/dev/test split files were not all found upstream.")
        total = sum(int(item.get("size", -1)) for item in selected)
        if total < 0 or total > args.max_bytes:
            parser.error(f"FinQA selected splits report {total} bytes, above the configured {args.max_bytes}-byte limit.")
        usage = shutil.disk_usage(args.output.parent if args.output.parent.exists() else ROOT)
        if usage.free < total * 2:
            parser.error(f"Need approximately {total * 2} bytes of free disk for downloads and validation; found {usage.free}.")
        args.output.mkdir(parents=True, exist_ok=True)
        file_records = []
        for item in sorted(selected, key=lambda row: row["name"]):
            target = args.output / item["name"]
            download_resumable(item["download_url"], target, int(item["size"]), args.max_bytes)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            file_records.append({"name": target.name, "bytes": target.stat().st_size,
                                 "sha256": digest, "upstream_url": item["html_url"]})
    except (URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        parser.error(f"Could not acquire FinQA from GitHub: {exc}. Check network access and rerun; partial downloads can resume.")
    manifest = {
        "dataset_id": "finqa",
        "version": revision,
        "license": "CC-BY-4.0 per IBM Research Hugging Face dataset card; review underlying SEC report provenance and rights",
        "source_url": "https://github.com/czyssrs/FinQA",
        "dataset_card_url": "https://huggingface.co/datasets/ibm-research/finqa",
        "acquired_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "private_test_excluded": True,
        "files": file_records,
        "permitted_use": "Financial question answering and numerical reasoning research after attribution and source-rights review.",
        "limitations": ["English annual-report reasoning is not Indian retail-bank agreement coverage.",
                        "Do not index as current regulatory guidance.",
                        "Dataset annotations license does not erase source-document rights."],
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Acquired and hashed {len(file_records)} FinQA split files ({total} bytes) to {args.output}.")


if __name__ == "__main__":
    main()
