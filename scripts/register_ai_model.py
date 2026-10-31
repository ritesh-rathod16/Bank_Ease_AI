"""Register an already-installed Ollama conversation model; never downloads models."""
import argparse
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="BankEase registry name")
    parser.add_argument("--ollama-model", required=True, help="Exact installed Ollama tag")
    parser.add_argument("--base-model", required=True, help="Upstream model and revision")
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifact-path", default="", help="Local adapter or GGUF path, if applicable")
    parser.add_argument("--format", default="ollama", help="For example ollama-gguf or ollama-adapter")
    parser.add_argument("--license", required=True, dest="license_name")
    parser.add_argument("--evaluation-status", default="not_evaluated")
    parser.add_argument("--registry", type=Path, default=ROOT / "config" / "ai_models.json")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urlopen(f"{base_url}/api/tags", timeout=3) as response:
            available = {item.get("name") for item in json.loads(response.read()).get("models", [])}
    except (URLError, TimeoutError, ValueError) as exc:
        parser.error(f"Ollama is unavailable at {base_url}: {exc}")
    if args.ollama_model not in available:
        parser.error(f"Model '{args.ollama_model}' is not installed. Pull or create it explicitly first; this script never downloads models.")

    registry_path = args.registry.resolve()
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"Could not read the model registry: {exc}")
    else:
        registry = {"schema_version": 1, "base_models": [], "models": []}
    registry.setdefault("schema_version", 1)
    registry.setdefault("base_models", [])
    registry.setdefault("models", [])
    entry = {
        "name": args.name,
        "base_model": args.base_model,
        "version": args.version,
        "task": "conversation",
        "provider": "ollama",
        "ollama_model": args.ollama_model,
        "artifact_path": args.artifact_path,
        "format": args.format,
        "license": args.license_name,
        "evaluation_status": args.evaluation_status,
        "chat_selectable": True,
    }
    existing = [index for index, item in enumerate(registry["models"])
                if item.get("name") == args.name]
    if existing and not args.replace:
        parser.error(f"Registry already contains '{args.name}'. Use --replace to update that entry.")
    if existing:
        registry["models"][existing[0]] = entry
    else:
        registry["models"].append(entry)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Registered '{args.name}' for conversation in {registry_path}. This confirms availability only, not model quality.")


if __name__ == "__main__":
    main()
