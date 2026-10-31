"""Run a transparent, synthetic-only generation check for a saved Qwen PEFT adapter."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from train_chat_adapter import (  # noqa: E402
    TrainingError,
    _sample_rows,
    load_corpus,
)


def _ids(tokenizer, messages: list[dict], *, add_generation_prompt: bool) -> list[int]:
    result = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=add_generation_prompt,
        enable_thinking=False,
        return_dict=True,
    )
    ids = result["input_ids"] if isinstance(result, Mapping) else result
    if hasattr(ids, "tolist"):
        ids = ids.tolist()
    if ids and isinstance(ids[0], list):
        if len(ids) != 1:
            raise TrainingError("Expected one conversation per tokenization call.")
        ids = ids[0]
    return list(ids)


def score_answer(expected_value: str | None, expected_page: int, answer: str) -> dict:
    folded = answer.casefold()
    if expected_value:
        return {
            "field_value_exact_substring": expected_value.casefold() in folded,
            "page_citation_present": f"[d1, page {expected_page}]" in folded,
            "abstention_phrase_present": None,
        }
    return {
        "field_value_exact_substring": None,
        "page_citation_present": None,
        "abstention_phrase_present": any(
            phrase in folded for phrase in ("can't confirm", "cannot confirm", "not provided")
        ),
    }


def evaluate(args: argparse.Namespace) -> dict:
    manifest, examples = load_corpus(args.dataset, allow_synthetic_research=True)
    adapter_report_path = args.adapter / "experiment.json"
    try:
        adapter_report = json.loads(adapter_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingError("Saved adapter has no readable experiment.json report.") from exc
    manifest_digest = hashlib.sha256((args.dataset / "manifest.json").read_bytes()).hexdigest()
    if adapter_report.get("dataset_manifest_sha256") != manifest_digest:
        raise TrainingError("Adapter report does not match this dataset manifest.")
    if adapter_report.get("model") != args.base_model or adapter_report.get("revision") != args.revision:
        raise TrainingError("Adapter base model or revision does not match the requested checkpoint.")
    if adapter_report.get("training_approved") is not False:
        raise TrainingError("This evaluator accepts only explicitly research-only adapters.")

    rows = examples["test"]
    if args.limit:
        rows = _sample_rows(rows, args.limit, args.seed)
    if not rows:
        raise TrainingError("No held-out test rows were selected.")

    try:
        import psutil
        import torch
        from huggingface_hub import snapshot_download
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise TrainingError("Install the isolated research dependencies from requirements-ml.txt.") from exc

    available_gib = psutil.virtual_memory().available / 1024**3
    if available_gib < args.min_free_memory_gib:
        raise TrainingError(
            f"Only {available_gib:.2f} GiB RAM is available; need {args.min_free_memory_gib:.2f} GiB."
        )
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    torch.set_num_threads(max(1, min(args.cpu_threads, os.cpu_count() or 1)))
    if torch.cuda.is_available():
        device = torch.device("cuda")
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    else:
        device = torch.device("cpu")
        dtype = torch.float32

    model_dir = Path(snapshot_download(
        repo_id=args.base_model,
        revision=args.revision,
        cache_dir=args.hf_cache,
        local_files_only=True,
    ))
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        model_dir,
        dtype=dtype,
        low_cpu_mem_usage=True,
        local_files_only=True,
        attn_implementation="eager",
    )
    model = PeftModel.from_pretrained(model, args.adapter, local_files_only=True)
    model.config.use_cache = True
    model.to(device)
    model.eval()

    predictions = []
    with torch.inference_mode():
        for index, row in enumerate(rows, start=1):
            messages = row["messages"]
            prompt_ids = _ids(tokenizer, messages[:2], add_generation_prompt=True)
            input_ids = torch.tensor([prompt_ids], dtype=torch.long, device=device)
            generated = model.generate(
                input_ids=input_ids,
                attention_mask=torch.ones_like(input_ids),
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                eos_token_id=tokenizer.eos_token_id,
                pad_token_id=tokenizer.pad_token_id,
                use_cache=True,
            )
            answer = tokenizer.decode(generated[0, input_ids.shape[1]:], skip_special_tokens=True)
            user_text = messages[1]["content"]
            page_match = re.search(r"\[D1, page (\d+)\]", user_text)
            expected_page = int(page_match.group(1)) if page_match else 1
            metadata = row["metadata"]
            scores = score_answer(metadata.get("expected_value"), expected_page, answer)
            predictions.append({
                "document_id": metadata["document_id"],
                "source_family": metadata["source_family"],
                "field_name": metadata.get("field_name"),
                "expected_value": metadata.get("expected_value"),
                "expected_page": expected_page,
                "answer": answer,
                "scores": scores,
            })
            print(f"Generated held-out answer {index}/{len(rows)}", flush=True)

    def summarize(key: str) -> dict:
        values = [item["scores"][key] for item in predictions if item["scores"][key] is not None]
        correct = sum(values)
        return {"correct": correct, "total": len(values), "rate": correct / max(len(values), 1)}

    result = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.base_model,
        "revision": args.revision,
        "adapter": str(args.adapter),
        "dataset_manifest_sha256": manifest_digest,
        "split": "test",
        "source_examples_by_split": manifest["example_counts"],
        "examples_evaluated": len(predictions),
        "device": str(device),
        "dtype": str(dtype),
        "max_new_tokens": args.max_new_tokens,
        "metrics": {
            "field_value_exact_substring": summarize("field_value_exact_substring"),
            "page_citation_present": summarize("page_citation_present"),
            "abstention_phrase_present": summarize("abstention_phrase_present"),
        },
        "production_ready": False,
        "training_approved": False,
        "limitations": [
            "The corpus and labels are generated fictional examples, not human-reviewed banking data.",
            "The benchmark is small and uses synthetic templates; results do not establish real-document quality.",
            "Exact-substring and citation-presence checks do not validate semantic correctness or citation support.",
        ],
        "predictions": predictions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/runtime/ml/chat-evidence-sft-v3")
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--revision", default="c1899de289a04d12100db370d81485cdf75e47ca")
    parser.add_argument("--hf-cache", type=Path, default=ROOT / "data/runtime/hf-cache")
    parser.add_argument("--limit", type=int, default=2, help="Held-out examples to generate; zero evaluates the entire test split.")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--min-free-memory-gib", type=float, default=3.5)
    parser.add_argument("--allow-synthetic-research", action="store_true")
    args = parser.parse_args()
    if not args.allow_synthetic_research:
        parser.error("This adapter/data are research-only; pass --allow-synthetic-research to continue.")
    if args.limit < 0 or args.max_new_tokens < 1 or args.cpu_threads < 1:
        parser.error("Limit, max-new-tokens, and CPU thread count are out of range.")
    result = evaluate(args)
    print(json.dumps({
        "output": str(args.output),
        "examples_evaluated": result["examples_evaluated"],
        "metrics": result["metrics"],
        "production_ready": result["production_ready"],
    }, indent=2))


if __name__ == "__main__":
    main()
