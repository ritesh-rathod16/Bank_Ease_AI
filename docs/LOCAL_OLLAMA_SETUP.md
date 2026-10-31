# Local Ollama setup (Windows)

The API checks Ollama at `http://127.0.0.1:11434` by default. It lists installed models and never pulls one automatically. In this workspace, the portable runtime and Qwen3 4B Instruct are already installed under ignored `data/runtime/ollama`; the model responded to general and document-grounded smoke prompts.

1. The setup helper can use a workspace-local Ollama runtime under `data/runtime/ollama/windows`, or installed Ollama on `PATH`. The portable server keeps its key and models under ignored `data/runtime/ollama` and disables Ollama cloud features.
2. Inspect the machine and model status without downloading anything:

   ```powershell
   python scripts/check_ai_models.py
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_ollama.ps1
   ```

3. Review the model card and license. The audited machine currently has `qwen3:4b-instruct` installed. The API uses the configured `OLLAMA_MODEL` value and reports model availability from Ollama at runtime. The API defaults to a 4K context window to reduce memory use.
4. To download it, explicitly run:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_ollama.ps1 -StartServer -Pull -Model qwen3:4b-instruct
   ```

   `-Pull` is the only path in the helper that runs `ollama pull`. The application never pulls a model automatically.
5. Set `OLLAMA_MODEL=qwen3:4b-instruct` in `.env`, restart `npm run dev:all`, sign in, and confirm `/api/v1/ai/status` reports `model_available: true`.

Ollama's API uses `GET /api/tags` for model discovery and `POST /api/chat` for streamed and non-streamed chat. BankEase uses the local API only. It does not expose Ollama directly through the browser.

## Register a compatible fine-tuned chat model

First create or import a chat-compatible model in Ollama yourself. For a GGUF model, follow Ollama's current `Modelfile` and `ollama create` instructions, then verify its tag with `ollama list`. Registration does not convert, download, or evaluate the model:

```powershell
python scripts/register_ai_model.py `
  --name bankease-chat-adapter-v1 `
  --ollama-model bankease-chat-adapter:latest `
  --base-model qwen3:4b-instruct `
  --version 1 `
  --format ollama-gguf `
  --license "record the actual model license" `
  --evaluation-status not_evaluated
```

The installed model appears in the assistant's local-model selector. Registration means only that the local tag exists. Evaluate it on held-out, human-reviewed examples before relying on its answers. A text-classification adapter such as the existing synthetic GPT-2 classifier is not a conversational model and must not be registered as one.

Official model details: [Ollama Qwen3 4B Instruct](https://ollama.com/library/qwen3%3A4b-instruct). API references: [Ollama chat](https://docs.ollama.com/api/chat), [Ollama model listing](https://docs.ollama.com/api/tags).
