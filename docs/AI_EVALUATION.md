# AI engine evaluation

## Current verified scope

- 2026-10-29 full regression run after FinQA preparation changes: `315 passed` across the backend suite, including family-disjoint repartitioning and acquisition network-error handling.
- 2026-10-30 regression run after adding the synthetic chat-SFT data checks: `317 passed` across `backend/tests`; the SFT-specific module passes `9/9`.
- FinQA's 8,281 examples are downloaded and prepared into company/year family-disjoint 80/10/10 splits. The upstream split had 355 of 799 report families across partitions. The prepared manifest marks training as unapproved pending source rights review; no new training run or model artifact was produced.
- Unit tests cover local/hosted routing rules, document-sharing consent, missing Ollama/model behavior, citation-label filtering, untrusted passage framing, hosted credential handling, and rate-limit mapping.
- API tests cover general conversations without document IDs, SSE deltas/final persistence, authenticated status, and existing owner-scoped document Q&A regressions.
- Live local Ollama inference was smoke-tested with Qwen3 1.7B: one general answer, one synthetic document answer with a valid `[D1]` citation, and one streamed response. This is a functionality check, not a quality benchmark. Hosted inference, SearXNG search, and a fine-tuned conversation-model benchmark remain unverified.
- A separate authenticated synthetic-PDF workflow was exercised end to end: upload, processing, owner-scoped streamed question, persisted answer, and a cited page/value. This is an integration smoke check, not a model quality score.
- Qwen3-0.6B (Apache-2.0, cached at revision `c1899de289a04d12100db370d81485cdf75e47ca`) now has a synthetic-only, family-disjoint chat-SFT preparation pipeline and LoRA runner. The corpus has 1,269/270/270 train/validation/test examples across 47/10/10 source families. The first BF16 CPU smoke was stopped after more than eight CPU minutes without completing a step. A one-step FP32 tooling smoke completed (training loss 0.6833, one-example validation loss 0.8037, generation disabled). A separate 32-step FP32 run completed in 243 seconds with 32 training examples and 32 validation examples. Its two generated held-out answers contained the expected value in 2/2 and expected page citation in 1/2; only two examples and 16 new tokens were evaluated, so this is not a quality benchmark. The adapter is smoke-limited, synthetic-only, and not registered for inference.
- The AI engine does not report numerical confidence scores. BM25 retrieval score is retained only for document source ranking.

## Required gold evaluation before relying on a conversational fine-tune

Build a human-reviewed, source-family-separated set for general instruction following, English/Hindi/Marathi explanations, numerical reasoning, loan clauses, insurance exclusions, statement reconciliation, document question answering, prompt injection, and abstention. Keep both document families and repeated question templates within one split.

Measure local and hosted models separately: exact normalized numeric answer accuracy, evidence passage/page recall, citation membership and citation support rate, abstention precision/recall, unsupported-claim rate, prompt-injection resistance, language quality, time-to-first-token, end-to-end latency, RAM/VRAM, and cost consumed. Report counts, errors and confidence intervals where sample size supports them. Keep deterministic EMI and balance calculations outside generative scoring.

The existing synthetic document classifier result is recorded in `ML_TRAINING.md`; it is not evidence for conversation quality.
