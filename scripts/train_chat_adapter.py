"""LoRA fine-tune a small chat model on an explicitly synthetic BankEase corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import time
from collections.abc import Mapping
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "runtime" / "ml" / "chat-evidence-sft-v3"
DEFAULT_OUTPUT = ROOT / "data" / "runtime" / "ml" / "qwen3-0.6b-evidence-lora-v1"
DEFAULT_MODEL = "Qwen/Qwen3-0.6B"
DEFAULT_REVISION = "c1899de289a04d12100db370d81485cdf75e47ca"


class TrainingError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sample_rows(rows: list[dict], limit: int, seed: int) -> list[dict]:
    if limit <= 0 or limit >= len(rows):
        return list(rows)
    return random.Random(seed).sample(rows, limit)


def load_corpus(directory: Path, allow_synthetic_research: bool) -> tuple[dict, dict[str, list[dict]]]:
    directory = directory.resolve(strict=True)
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingError("Could not read the prepared corpus manifest.") from exc
    if manifest.get("dataset_id") != "bankease-synthetic-evidence-chat-v1":
        raise TrainingError("This runner accepts only the reviewed BankEase evidence-chat data format.")
    if manifest.get("training_approved") is not True and not allow_synthetic_research:
        raise TrainingError("Corpus is not training-approved. For a research-only run, pass --allow-synthetic-research.")
    if manifest.get("synthetic_only") is not True or manifest.get("customer_data_included") is not False:
        raise TrainingError("Training is allowed here only for synthetic data with no customer records.")
    examples: dict[str, list[dict]] = {}
    family_splits: dict[str, set[str]] = {}
    seen_ids: set[str] = set()
    for split in ("train", "validation", "test"):
        filename = f"{split}.jsonl"
        path = directory / filename
        try:
            content = path.read_bytes()
            expected_digest = manifest.get("files", {}).get(filename, {}).get("sha256")
            if not expected_digest or sha256(content) != expected_digest:
                raise TrainingError(f"Checksum mismatch for {filename}.")
            rows = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrainingError(f"Could not read valid {filename}.") from exc
        if not rows:
            raise TrainingError(f"The {split} split is empty.")
        for row in rows:
            metadata = row.get("metadata", {})
            messages = row.get("messages", [])
            if (metadata.get("synthetic") is not True or metadata.get("human_reviewed") is not False
                    or metadata.get("split") != split or not metadata.get("source_family")
                    or len(messages) != 3
                    or [message.get("role") for message in messages] != ["system", "user", "assistant"]):
                raise TrainingError(f"Malformed, non-synthetic, or cross-split example in {filename}.")
            family_splits.setdefault(metadata["source_family"], set()).add(split)
            identity = (split, metadata.get("document_id"), metadata.get("field_name"),
                        messages[1].get("content", ""))
            key = sha256(json.dumps(identity, sort_keys=True).encode())
            if key in seen_ids:
                raise TrainingError(f"Duplicate training example in {filename}.")
            seen_ids.add(key)
        examples[split] = rows
    if any(len(splits) != 1 for splits in family_splits.values()):
        raise TrainingError("A source family appears in multiple data splits.")
    if len(examples["train"]) <= len(examples["validation"]):
        raise TrainingError("Training split must be larger than the validation split.")
    return manifest, examples


def _encoded_rows(rows: list[dict], tokenizer, max_length: int) -> list[dict[str, Any]]:
    encoded = []

    def token_ids(messages: list[dict], add_generation_prompt: bool) -> list[int]:
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

    for index, row in enumerate(rows):
        messages = row["messages"]
        prompt_ids = token_ids(messages[:2], add_generation_prompt=True)
        full_ids = token_ids(messages, add_generation_prompt=False)
        if full_ids[:len(prompt_ids)] != prompt_ids:
            raise TrainingError("Tokenizer chat template does not preserve the assistant generation prefix.")
        if len(full_ids) > max_length:
            raise TrainingError(
                f"Example {index} needs {len(full_ids)} tokens, above --max-length {max_length}; refusing silent truncation."
            )
        labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids):]
        if not any(token != -100 for token in labels):
            raise TrainingError(f"Example {index} has no supervised assistant tokens.")
        encoded.append({"input_ids": full_ids, "labels": labels, "metadata": row["metadata"],
                        "prompt_ids": prompt_ids})
    return encoded


def _collate(batch: list[dict], pad_id: int, torch):
    width = max(len(row["input_ids"]) for row in batch)
    input_ids = torch.full((len(batch), width), pad_id, dtype=torch.long)
    labels = torch.full((len(batch), width), -100, dtype=torch.long)
    mask = torch.zeros((len(batch), width), dtype=torch.long)
    for index, row in enumerate(batch):
        size = len(row["input_ids"])
        input_ids[index, :size] = torch.tensor(row["input_ids"], dtype=torch.long)
        labels[index, :size] = torch.tensor(row["labels"], dtype=torch.long)
        mask[index, :size] = 1
    return {"input_ids": input_ids, "attention_mask": mask, "labels": labels}


def _evaluate_loss(model, rows: list[dict], pad_id: int, batch_size: int, torch, device) -> float:
    model.eval()
    loss_total = 0.0
    token_total = 0
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            batch = _collate(rows[start:start + batch_size], pad_id, torch)
            batch = {key: value.to(device) for key, value in batch.items()}
            output = model(**batch)
            tokens = int((batch["labels"] != -100).sum().item())
            loss_total += float(output.loss) * tokens
            token_total += tokens
    model.train()
    return loss_total / max(token_total, 1)


def _generate_eval(model, tokenizer, rows: list[dict], limit: int, max_new_tokens: int,
                   torch, device) -> dict:
    sample = rows[:limit]
    if not sample:
        return {"examples": 0}
    model.eval()
    value_hits = citation_hits = abstention_hits = positive_count = abstention_count = 0
    with torch.inference_mode():
        for index, row in enumerate(sample, start=1):
            tokens = torch.tensor([row["prompt_ids"]], dtype=torch.long, device=device)
            attention = torch.ones_like(tokens)
            generated = model.generate(
                input_ids=tokens,
                attention_mask=attention,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                eos_token_id=tokenizer.eos_token_id,
                pad_token_id=tokenizer.pad_token_id,
                use_cache=True,
            )
            answer = tokenizer.decode(generated[0, tokens.shape[1]:], skip_special_tokens=True)
            metadata = row["metadata"]
            expected = metadata.get("expected_value")
            if expected:
                positive_count += 1
                value_hits += str(expected).casefold() in answer.casefold()
                citation_hits += "[d1, page" in answer.casefold()
            else:
                abstention_count += 1
                abstention_hits += ("can't confirm" in answer.casefold()
                                    or "cannot confirm" in answer.casefold()
                                    or "not provided" in answer.casefold())
            if index % 5 == 0:
                print(f"Held-out generation {index}/{len(sample)}", flush=True)
    return {
        "examples": len(sample),
        "field_value_exact_substring": {"correct": value_hits, "total": positive_count,
                                         "rate": value_hits / max(positive_count, 1)},
        "page_citation_present": {"correct": citation_hits, "total": positive_count,
                                  "rate": citation_hits / max(positive_count, 1)},
        "abstention_phrase_present": {"correct": abstention_hits, "total": abstention_count,
                                      "rate": abstention_hits / max(abstention_count, 1)},
    }


def train(args: argparse.Namespace) -> dict:
    dataset_manifest, raw_examples = load_corpus(args.dataset, args.allow_synthetic_research)
    original_counts = {name: len(rows) for name, rows in raw_examples.items()}
    if args.max_examples_per_split:
        raw_examples = {
            name: _sample_rows(rows, args.max_examples_per_split, args.seed + index)
            for index, (name, rows) in enumerate(raw_examples.items())
        }
        if any(not rows for rows in raw_examples.values()):
            raise TrainingError("--max-examples-per-split is too small to populate every split.")
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty model artifact directory: {args.output}")
    try:
        import psutil
        import torch
        from huggingface_hub import snapshot_download
        from peft import LoraConfig, TaskType, get_peft_model
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise TrainingError("Install the isolated research dependencies from requirements-ml.txt.") from exc

    available_memory_gib = psutil.virtual_memory().available / (1024 ** 3)
    if available_memory_gib < args.min_free_memory_gib:
        raise TrainingError(
            f"Only {available_memory_gib:.2f} GiB RAM is available; need {args.min_free_memory_gib:.2f} GiB. "
            "Close memory-heavy workloads and retry."
        )
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(max(1, min(args.cpu_threads, os.cpu_count() or 1)))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
        device = torch.device("cuda")
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    else:
        device = torch.device("cpu")
        # AVX2-only Windows CPUs can emulate BF16 poorly; FP32 uses optimized CPU kernels.
        dtype = torch.float32
    model_dir = Path(snapshot_download(
        repo_id=args.base_model, revision=args.revision, cache_dir=args.hf_cache,
        local_files_only=True,
    ))
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    encoded = {name: _encoded_rows(rows, tokenizer, args.max_length)
               for name, rows in raw_examples.items()}
    args.output.mkdir(parents=True, exist_ok=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_dir,
        dtype=dtype,
        low_cpu_mem_usage=True,
        local_files_only=True,
        attn_implementation="eager",
    )
    model.config.use_cache = False
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    else:
        model.gradient_checkpointing_disable()
    lora = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=["q_proj", "v_proj"],
    )
    model = get_peft_model(model, lora)
    model.to(device)
    trainable = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    total = sum(parameter.numel() for parameter in model.parameters())
    print(f"Training on {device} ({dtype}); {trainable:,}/{total:,} trainable parameters; "
          f"{len(encoded['train'])} train examples; {available_memory_gib:.2f} GiB RAM available.", flush=True)
    optimizer = torch.optim.AdamW(
        (parameter for parameter in model.parameters() if parameter.requires_grad),
        lr=args.learning_rate,
        weight_decay=args.weight_decay,
    )
    history = []
    best_validation_loss = float("inf")
    started = time.time()
    steps_per_epoch = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        order = list(range(len(encoded["train"])))
        random.Random(args.seed + epoch).shuffle(order)
        losses = []
        for batch_index, start in enumerate(range(0, len(order), args.batch_size), start=1):
            rows = [encoded["train"][index] for index in order[start:start + args.batch_size]]
            batch = _collate(rows, tokenizer.pad_token_id, torch)
            batch = {key: value.to(device) for key, value in batch.items()}
            optimizer.zero_grad(set_to_none=True)
            loss = model(**batch).loss
            if not torch.isfinite(loss):
                raise TrainingError(f"Non-finite loss at epoch {epoch}, batch {batch_index}.")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                (parameter for parameter in model.parameters() if parameter.requires_grad), 1.0
            )
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
            if (batch_index % args.log_every == 0
                    or batch_index == len(range(0, len(order), args.batch_size))
                    or (args.max_train_steps and batch_index >= args.max_train_steps)):
                print(f"Epoch {epoch}/{args.epochs}, batch {batch_index}: loss={losses[-1]:.4f}", flush=True)
            if args.max_train_steps and batch_index >= args.max_train_steps:
                break
        steps_per_epoch.append(len(losses))
        validation_loss = _evaluate_loss(
            model, encoded["validation"], tokenizer.pad_token_id, args.batch_size, torch, device
        )
        record = {"epoch": epoch, "mean_training_loss": sum(losses) / max(len(losses), 1),
                  "validation_loss": validation_loss}
        history.append(record)
        print(f"Epoch {epoch} complete; validation loss={validation_loss:.4f}", flush=True)
        if validation_loss <= best_validation_loss:
            best_validation_loss = validation_loss
            model.save_pretrained(args.output)
            tokenizer.save_pretrained(args.output)

    model.config.use_cache = True
    report = {
        "model": args.base_model,
        "revision": args.revision,
        "base_license": "Apache-2.0 (official Hugging Face model card)",
        "adapter_format": "PEFT LoRA causal language modeling",
        "task": "synthetic evidence-grounded banking document Q&A and abstention behavior",
        "dataset_id": dataset_manifest["dataset_id"],
        "dataset_manifest_sha256": sha256((args.dataset / "manifest.json").read_bytes()),
        "synthetic_only": True,
        "human_reviewed": False,
        "production_ready": False,
        "training_approved": False,
        "device": str(device),
        "dtype": str(dtype),
        "trainable_parameters": trainable,
        "total_parameters": total,
        "train_examples": len(encoded["train"]),
        "validation_examples": len(encoded["validation"]),
        "test_examples": len(encoded["test"]),
        "source_examples_by_split": original_counts,
        "training_steps_per_epoch": steps_per_epoch,
        "smoke_limited_run": bool(args.max_examples_per_split or args.max_train_steps),
        "max_length": args.max_length,
        "epochs": args.epochs,
        "seed": args.seed,
        "epoch_history": history,
        "held_out_generation": _generate_eval(
            model, tokenizer, encoded["test"], args.generation_eval_limit,
            args.max_new_tokens, torch, device,
        ),
        "elapsed_seconds": round(time.time() - started, 2),
        "limitations": [
            "All examples are generated from fictional repository documents; no customer records were used.",
            "Labels are construction-derived and not human reviewed.",
            "The source families reuse three generated layout templates and do not represent banking documents in the wild.",
            "This adapter is experimental and is not integrated into the production chat router.",
            "Generation metrics are a small synthetic smoke evaluation, not a quality or safety benchmark.",
        ],
    }
    (args.output / "experiment.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    card = (
        "# BankEase synthetic evidence chat adapter\n\n"
        "Research-only PEFT LoRA adapter for Qwen3-0.6B. It was trained exclusively on generated fictional "
        "BankEase fixture documents. Labels are construction-derived and not human reviewed. This adapter "
        "is not production-ready, is not evidence of real-document performance, and is not connected to "
        "the customer chat router. See `experiment.json` for the measured run and limitations.\n\n"
        "Base model: https://huggingface.co/Qwen/Qwen3-0.6B\n"
        f"Base revision: `{args.revision}`\n"
        "Base license: Apache-2.0. Review the base and adapter terms before redistribution.\n"
    )
    (args.output / "README.md").write_text(card, encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--hf-cache", type=Path, default=ROOT / "data" / "runtime" / "hf-cache")
    parser.add_argument("--allow-synthetic-research", action="store_true",
                        help="Explicitly allow the unreviewed synthetic-only corpus for research.")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--lora-rank", type=int, default=4)
    parser.add_argument("--lora-alpha", type=int, default=8)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--learning-rate", type=float, default=8e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--min-free-memory-gib", type=float, default=3.5)
    parser.add_argument("--generation-eval-limit", type=int, default=24)
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--max-examples-per-split", type=int, default=0,
                        help="Limit each split for a smoke run; zero uses the full corpus.")
    parser.add_argument("--max-train-steps", type=int, default=0,
                        help="Limit optimizer steps per epoch for a smoke run; zero uses the full split.")
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--gradient-checkpointing", action="store_true",
                        help="Trade speed for lower activation memory when accelerator memory is tight.")
    args = parser.parse_args()
    if not args.allow_synthetic_research:
        parser.error("This dataset is not approved for product training; pass --allow-synthetic-research for an isolated experiment.")
    if (args.epochs < 1 or args.max_length < 64 or args.batch_size < 1
            or args.generation_eval_limit < 0 or args.max_examples_per_split < 0
            or args.max_train_steps < 0 or args.log_every < 1):
        parser.error("Epochs, max length, batch size, and evaluation limit are out of range.")
    try:
        report = train(args)
    except (TrainingError, FileExistsError) as exc:
        parser.exit(status=2, message=f"Training blocked: {exc}\n")
    print(json.dumps({"output": str(args.output), "validation_loss": report["epoch_history"][-1]["validation_loss"],
                      "held_out_generation": report["held_out_generation"],
                      "production_ready": report["production_ready"]}, indent=2))


if __name__ == "__main__":
    main()
