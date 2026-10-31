# BankEase AI engine

## Request flow

```text
Next.js assistant
  -> authenticated FastAPI conversation endpoint
  -> owner checks for conversation and selected documents
  -> owner-scoped BM25 retrieval by PDF page, DOCX extracted section, or statement row (document mode)
  -> optional configured SearXNG retrieval (user-selected)
  -> local-first provider router
  -> citation-label validation
  -> owner-scoped message persistence
```

`POST /api/v1/conversations/{id}/messages/stream` streams text as server-sent events. The original non-streaming message route remains available. `GET /api/v1/ai/status` reports Ollama, configured hosted provider, configured search service, installed models, and registry entries without returning credentials.

General conversations may have no document IDs. Document conversations retain the selected owner-scoped document set and retrieve evidence passages before prompting a model. PDF evidence cites pages; DOCX text cites extracted sections because Word pagination is not preserved; statements cite worksheet rows or source pages. If a local model is unavailable, document questions retain the deterministic extractive fallback; general questions return a clear unavailable-provider error instead of a fabricated answer.

## Routing modes

- `local`: installed local Ollama model only.
- `hosted`: configured Hugging Face Inference Providers model, with per-message hosted permission.
- `auto`: local first. Hosted fallback requires server configuration and per-message permission. Selected document content additionally requires the per-message document-consent flag.
- `hybrid`: selected-document questions stay local. General questions prefer the explicitly allowed hosted provider, then local.

Hosted routing is disabled by default and may incur paid credits; it is not part of the ₹0 workflow. No frontend request connects directly to Ollama or includes provider credentials. Hosted general-chat history is sent only with the hosted-permission flag; history from document conversations is not sent to hosted models. Document passages are sent to hosted inference only when both hosted inference and document sharing are explicitly enabled for that message. Never enter personal or sensitive details into unpaid hosted inference.

## Retrieval and evidence

The document retriever is the existing BM25 implementation. It returns a supporting passage and format-appropriate location; the model sees only these retrieved excerpts, each labelled as untrusted data. Web research is an optional SearXNG JSON endpoint selected by the user. It searches only the current question, has a six-second timeout, and returns at most five HTTP(S) citations. It does not silently send uploaded document content to the search service.

Model citation labels are filtered against the retrieved source IDs. Source cards in the UI remain generated from backend retrieval records, not model-written URLs. This is label validation, not semantic proof that every generated sentence is supported. Review important financial and legal details against the cited original page.

## Fine-tuned model separation

The model registry is `config/ai_models.json` (or `BANKEASE_MODEL_REGISTRY`). It separates conversational Ollama models from other tasks. An installed converted fine-tune can be registered with `scripts/register_ai_model.py`; registration checks that its Ollama tag exists but does not claim quality. The existing GPT-2 LoRA artifact is a synthetic-only sequence-classification adapter and is deliberately not exposed as a chat model. It remains outside customer inference.

Training artifacts and acquired datasets belong under ignored `data/runtime/ml/`. They are never mixed with customer documents. Public dataset acquisition is an explicit offline-capable workflow described in `ML_DATASETS.md`; nothing is downloaded at API startup.
