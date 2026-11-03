# AI provider setup

## Defaults

The application uses local Ollama by default. Hosted inference is optional and disabled by default. The frontend calls the BankEase FastAPI routes only.

Copy `.env.example` to `.env`, then configure the local settings if needed:

```dotenv
AI_PROVIDER=local
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:1.7b
OLLAMA_FALLBACK_MODELS=qwen3:0.6b,llama3.2:1b-instruct
OLLAMA_NUM_CTX=4096
```

`scripts/dev.py` loads simple `.env` values without printing them. Environment variables already set by the shell take precedence. `GET /api/v1/ai/status` requires a signed-in user and reports whether Ollama is reachable and whether the configured model is installed.

## Hugging Face Inference Providers (optional)

The adapter uses Hugging Face's documented OpenAI-compatible chat-completions endpoint. Configure it only if you accept sending general chat prompts to that provider:

```dotenv
HOSTED_AI_ENABLED=true
HOSTED_AI_PROVIDER=huggingface
HOSTED_AI_MODEL=provider-supported/model-id
HOSTED_AI_API_KEY=your-server-side-token
```

The token stays on the backend. The application does not choose a model for you or silently switch providers. In the assistant, select Hosted, Auto, or Hybrid and check “Allow configured hosted inference” before sending. For a selected document, the separate document-sharing checkbox must also be checked. It defaults off.

Do not treat this as a free inference service. Hugging Face's current pricing documentation says free accounts have no included monthly credits and that requests require remaining credits; extra credits must be purchased. Pricing and eligibility can change, so verify the live account page before use. The application never buys credits, and hosted mode stays disabled unless the operator configures it and the user explicitly enables it per message. It is not part of the ₹0 local workflow. Avoid sending personal or sensitive details; document excerpts require a separate consent control and a review of provider terms.

Google Gemini's developer API also offers an unpaid tier for some models, but its current terms say unpaid inputs/outputs may be used to improve Google services and reviewed by humans, prohibit submitting sensitive, confidential, or personal information, and restrict the service to professional/business developer use rather than consumer use. BankEase's consumer-facing assistant does not use that unpaid route.

Official references: [Inference Providers pricing](https://huggingface.co/docs/inference-providers/en/pricing), [chat-completion API](https://huggingface.co/docs/inference-providers/en/tasks/chat-completion), [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing), [Gemini API terms](https://ai.google.dev/gemini-api/terms), [API overview](https://huggingface.co/docs/inference-providers/en/index).

## Web search (optional)

The web adapter is independent from the model provider. Configure a trusted SearXNG instance:

```dotenv
SEARXNG_BASE_URL=http://127.0.0.1:8080
```

The assistant's Web research control remains disabled until a URL is configured. SearXNG is not bundled or automatically launched. The search adapter uses its JSON endpoint, a bounded timeout, and a small result cap. It sends the user's question to the configured search service, not document excerpts. Without a configured search service, the UI reports web research as unavailable.

## Errors and charges

Missing Ollama/model, provider timeouts, rate limits, credentials, and empty responses return clear errors. Document Q&A can use deterministic extraction when no model is available. There are no automatic retries for paid/hosted requests and no automatic provider fallback for a document question without explicit consent.
