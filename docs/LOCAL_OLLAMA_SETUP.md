# Local Ollama setup (Windows)

The API checks Ollama at `http://127.0.0.1:11434` by default. It lists installed models and never pulls one automatically. For lightweight local use, the project recommends `qwen3:1.7b` as the primary model with `qwen3:0.6b` and `llama3.2:1b-instruct` as small fallback models.

1. Install Ollama for Windows. The simplest setup is:

   ```powershell
   winget install --id=Ollama.Ollama -e
   ```

   Then start the service:

   ```powershell
   ollama serve
   ```

2. Pull the recommended lightweight models:

   ```powershell
   ollama pull qwen3:1.7b
   ollama pull qwen3:0.6b
   ollama pull llama3.2:1b-instruct
   ```

3. Inspect the machine and model status without downloading anything:

   ```powershell
   python scripts/check_ai_models.py
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_ollama.ps1
   ```

4. Review the model card and license. The API uses the configured `OLLAMA_MODEL` value and reports model availability from Ollama at runtime. The API defaults to a 4K context window to reduce memory use.
5. Set your local model config in `.env`:

   ```env
   AI_PROVIDER=local
   OLLAMA_BASE_URL=http://127.0.0.1:11434
   OLLAMA_MODEL=qwen3:1.7b
   OLLAMA_FALLBACK_MODELS=qwen3:0.6b,llama3.2:1b-instruct
   ```

   Then restart `npm run dev:all`, sign in, and confirm `/api/v1/ai/status` reports `model_available: true`.

Ollama's API uses `GET /api/tags` for model discovery and `POST /api/chat` for streamed and non-streamed chat. BankEase uses the local API only. It does not expose Ollama directly through the browser.

## Register a compatible fine-tuned chat model

First create or import a chat-compatible model in Ollama yourself. For a GGUF model, follow Ollama's current `Modelfile` and `ollama create` instructions, then verify its tag with `ollama list`. Registration does not convert, download, or evaluate the model:

```powershell
python scripts/register_ai_model.py `
  --name bankease-chat-adapter-v1 `
  --ollama-model bankease-chat-adapter:latest `
  --base-model qwen3:1.7b `
  --version 1 `
  --format ollama-gguf `
  --license "record the actual model license" `
  --evaluation-status not_evaluated
```

The installed model appears in the assistant's local-model selector. Registration means only that the local tag exists. Evaluate it on held-out, human-reviewed examples before relying on its answers. A text-classification adapter such as the existing synthetic GPT-2 classifier is not a conversational model and must not be registered as one.

Official model details: [Ollama Qwen3 1.7B](https://ollama.com/library/qwen3%3A1.7b) and [Ollama Llama 3.2 1B Instruct](https://ollama.com/library/llama3.2:1b-instruct). API references: [Ollama chat](https://docs.ollama.com/api/chat), [Ollama model listing](https://docs.ollama.com/api/tags).
