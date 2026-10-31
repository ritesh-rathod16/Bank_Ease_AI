# AI privacy and security controls

- Conversations and documents use the existing authenticated FastAPI routes, session cookies, CSRF checks for writes, owner-scoped repository queries, and per-document ownership checks.
- The browser never contacts Ollama or an external inference provider directly. Model tokens stay in backend environment variables and are not returned from the status API.
- Hosted inference is disabled by default and requires server enablement plus per-message user permission. Sending selected document passages requires an additional, separate per-message consent. Hosted provider history is not sent for document-context conversations.
- Web research is independently disabled until a trusted SearXNG URL is configured and the user selects it. The search adapter sends only the question, not retrieved document passages. Search results and uploaded evidence are labelled untrusted and are never accepted as instructions.
- Retrieved evidence is scoped to the current owner's selected document IDs. Citation IDs are restricted to source IDs produced by the backend retriever. This checks label membership; it does not prove that the model's sentence follows from a passage.
- Model and source text are not written to ordinary request logs by the AI engine. Provider errors are sanitized.
- Customer records are never used for training by these scripts. Dataset artifacts and model checkpoints remain in a separate ignored runtime directory.
- Delete conversation removes its message history. Document and account deletion use existing cascades. Retention follows existing settings.

## Remaining launch review

This local development integration is not a production security certification. Before public banking-document use, review provider data-processing terms, user consent wording, deployment/network controls, quotas, prompt-injection red-team results, web-search retention, encryption/backups, applicable Indian privacy obligations, and security logging. No hosted document transmission should be enabled without that review and the required owner authorization.
