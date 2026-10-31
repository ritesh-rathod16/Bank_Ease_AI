"use client";

import { useEffect, useRef } from "react";
import { Check, LoaderCircle, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { AiStatus, ConversationSummary, DocumentSummary } from "@/types/api";
import { humanize } from "@/lib/utils";

export type AiMode = "local" | "hosted" | "auto" | "hybrid";

export type AiSettingsDraft = {
  mode: AiMode;
  model: string;
  allowHosted: boolean;
  allowHostedDocuments: boolean;
  webResearch: boolean;
};

type AiSettingsCardProps = {
  open: boolean;
  draft: AiSettingsDraft;
  aiStatus?: AiStatus;
  aiStatusLoading: boolean;
  aiStatusError: boolean;
  conversations: ConversationSummary[];
  selectedConversationId: string;
  onChooseConversation: (id: string) => void;
  documents: DocumentSummary[];
  answerMode: "general" | "selected" | "documents";
  selectedDocumentId: string;
  selectedDocumentIds: string[];
  onAnswerModeChange: (mode: "general" | "selected" | "documents") => void;
  onSelectDocument: (id: string) => void;
  onToggleAdditionalDocument: (id: string, checked: boolean) => void;
  onDraftChange: (change: Partial<AiSettingsDraft>) => void;
  onDeleteConversation: () => void;
  canDeleteConversation: boolean;
  onClose: () => void;
  onSave: () => void;
};

const labelClass = "grid gap-1.5 text-xs font-medium text-muted";
const selectClass = "h-10 w-full min-w-0 rounded-lg border border-line bg-panel-raised px-3 text-sm text-ink outline-none focus:border-teal focus:ring-2 focus:ring-teal/20";

export function AiSettingsCard({
  open,
  draft,
  aiStatus,
  aiStatusLoading,
  aiStatusError,
  conversations,
  selectedConversationId,
  onChooseConversation,
  documents,
  answerMode,
  selectedDocumentId,
  selectedDocumentIds,
  onAnswerModeChange,
  onSelectDocument,
  onToggleAdditionalDocument,
  onDraftChange,
  onDeleteConversation,
  canDeleteConversation,
  onClose,
  onSave,
}: AiSettingsCardProps) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const localModels = aiStatus?.local.models ?? [];
  const selectedModelInstalled = localModels.some((model) => model.name === draft.model);
  const modelAvailable = Boolean(aiStatus?.local.available && (draft.model === aiStatus.local.configured_model ? aiStatus.local.model_available : selectedModelInstalled));
  const webResearchConfigured = Boolean(aiStatus?.web_research_configured);
  const hostedAvailable = Boolean(aiStatus?.hosted.enabled && aiStatus.hosted.configured);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [open, onClose]);

  if (!open) return null;

  return <div
    className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/65 p-0 backdrop-blur-[2px] sm:items-center sm:p-4"
    role="presentation"
    onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
  >
    <section
      role="dialog"
      aria-modal="true"
      aria-labelledby="ai-settings-title"
      className="flex max-h-[min(90dvh,48rem)] w-full max-w-2xl flex-col overflow-hidden rounded-t-2xl border border-line bg-panel text-ink shadow-2xl sm:rounded-2xl"
    >
      <header className="flex shrink-0 items-center justify-between gap-4 border-b border-line px-5 py-4 sm:px-6">
        <div className="min-w-0">
          <p className="text-[10px] font-bold uppercase tracking-[.14em] text-teal">Preferences</p>
          <h2 id="ai-settings-title" className="mt-1 text-lg font-semibold">AI settings</h2>
          <p className="mt-1 text-xs leading-5 text-muted">Configure how the assistant answers and which sources it can use.</p>
        </div>
        <button ref={closeButton} type="button" onClick={onClose} aria-label="Close AI settings" className="grid size-9 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-panel-raised hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60"><X size={17} /></button>
      </header>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain p-4 sm:p-6">
        <section className="rounded-xl border border-line bg-panel-raised/60 p-4 sm:p-5">
          <div className="mb-4">
            <h3 className="text-sm font-semibold">AI Configuration</h3>
            <p className="mt-1 text-xs text-muted">Choose the model and optional research sources.</p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className={labelClass}>AI Mode
              <select className={selectClass} value={draft.mode} onChange={(event) => onDraftChange({ mode: event.target.value as AiMode })}>
                <option value="local">Local only</option>
                <option value="hosted">Hosted provider</option>
                <option value="auto">Auto (local first)</option>
                <option value="hybrid">Hybrid routing</option>
              </select>
            </label>
            {(draft.mode === "local" || draft.mode === "auto" || draft.mode === "hybrid") && <label className={labelClass}>Installed Local Model
              <select className={selectClass} value={draft.model} onChange={(event) => onDraftChange({ model: event.target.value })}>
                {draft.model && !selectedModelInstalled && <option value={draft.model}>{draft.model} · unavailable</option>}
                {!draft.model && <option value="">{localModels.length === 0 ? "No local model detected" : "Choose a local model"}</option>}
                {localModels.map((model) => <option key={model.name} value={model.name}>{model.name}{model.parameter_size ? ` · ${model.parameter_size}` : ""}</option>)}
              </select>
            </label>}
            <div className="flex items-center justify-between gap-3 rounded-lg border border-line bg-panel px-3 py-2.5">
              <span className="text-xs font-medium text-muted">Model Availability</span>
              <span className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold ${modelAvailable ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>
                {aiStatusLoading ? <span className="inline-flex items-center gap-1"><LoaderCircle size={11} className="animate-spin" />Checking</span> : modelAvailable ? <span className="inline-flex items-center gap-1"><Check size={11} />Available</span> : "Unavailable"}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3 rounded-lg border border-line bg-panel px-3 py-2.5">
              <span className="text-xs font-medium text-muted">Web Research</span>
              <span className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold ${webResearchConfigured ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>
                {webResearchConfigured ? "Configured" : "Not configured"}
              </span>
            </div>
          </div>

          {(draft.mode === "hosted" || draft.mode === "auto" || draft.mode === "hybrid") && <label className="mt-4 flex items-start gap-2.5 rounded-lg border border-line bg-panel p-3 text-xs leading-5 text-muted">
            <input type="checkbox" checked={draft.allowHosted} disabled={!hostedAvailable} onChange={(event) => onDraftChange({ allowHosted: event.target.checked })} className="mt-0.5 accent-blue-600" />
            <span>Allow configured hosted inference for this conversation. It may use paid credits.{!hostedAvailable && " A hosted provider is not configured."}</span>
          </label>}
          {selectedDocumentIds.length > 0 && (draft.mode === "hosted" || draft.mode === "auto") && <label className="mt-3 flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900">
            <input type="checkbox" checked={draft.allowHostedDocuments} onChange={(event) => onDraftChange({ allowHostedDocuments: event.target.checked })} className="mt-0.5 accent-blue-600" />
            <span>Allow selected document passages to be sent to the hosted provider.</span>
          </label>}
          <label className={`mt-3 flex items-start gap-2.5 rounded-lg border border-line bg-panel p-3 text-xs leading-5 ${webResearchConfigured ? "text-muted" : "text-muted/70"}`}>
            <input type="checkbox" checked={draft.webResearch} disabled={!webResearchConfigured} onChange={(event) => onDraftChange({ webResearch: event.target.checked })} className="mt-0.5 accent-blue-600" />
            <span>Use web research for this conversation.{!webResearchConfigured && " Configure a search service to enable it."}</span>
          </label>
          {aiStatusError && <p role="alert" className="mt-3 text-xs text-red-700">Could not check AI availability. Reload the page and try again.</p>}
        </section>

        <section className="rounded-xl border border-line bg-panel-raised/60 p-4 sm:p-5">
          <div className="mb-4">
            <h3 className="text-sm font-semibold">Conversation Settings</h3>
            <p className="mt-1 text-xs text-muted">Choose a saved conversation and its answer context.</p>
          </div>
          <div className="grid gap-4">
            <label className={labelClass}>Conversation History
              <select className={selectClass} value={selectedConversationId} onChange={(event) => onChooseConversation(event.target.value)}>
                <option value="">New conversation</option>
                {conversations.map((conversation) => <option key={conversation.id} value={conversation.id}>{conversation.title || conversation.document_filename}</option>)}
              </select>
            </label>
            <label className={labelClass}>Answer Using
              <select
                className={selectClass}
                value={answerMode}
                onChange={(event) => onAnswerModeChange(event.target.value as "general" | "selected" | "documents")}
              >
                <option value="general">General chat (no document)</option>
                <option value="selected" disabled={documents.length === 0}>Selected document</option>
                <option value="documents" disabled={documents.length === 0}>Document-based answers</option>
              </select>
            </label>
            {selectedDocumentIds.length > 0 && <label className={labelClass}>Selected Document
              <select className={selectClass} value={selectedDocumentId} onChange={(event) => onSelectDocument(event.target.value)}>
                {documents.map((document) => <option key={document.id} value={document.id}>{document.filename} · {humanize(document.doc_type)}</option>)}
              </select>
            </label>}
            {selectedDocumentIds.length > 0 && <details className="rounded-lg border border-line bg-panel p-3">
              <summary className="cursor-pointer text-xs font-medium text-ink">Also include documents ({selectedDocumentIds.length}/5 selected)</summary>
              <fieldset className="mt-3 max-h-40 space-y-1.5 overflow-y-auto" aria-label="Additional documents for this question">
                <legend className="sr-only">Choose up to four additional supported documents</legend>
                {documents.filter((document) => document.id !== selectedDocumentId).map((document) => <label key={document.id} className="flex min-w-0 items-start gap-2.5 rounded-md px-2 py-2 text-xs text-muted hover:bg-panel-raised">
                  <input type="checkbox" checked={selectedDocumentIds.includes(document.id)} disabled={!selectedDocumentIds.includes(document.id) && selectedDocumentIds.length >= 5} onChange={(event) => onToggleAdditionalDocument(document.id, event.target.checked)} className="mt-0.5 shrink-0 accent-blue-600" />
                  <span className="min-w-0"><span className="block break-words font-medium text-ink">{document.filename}</span><span className="text-[10px] text-muted">{humanize(document.doc_type)}</span></span>
                </label>)}
                {documents.length <= 1 && <p className="px-2 py-2 text-xs text-muted">No other supported documents are available.</p>}
              </fieldset>
            </details>}
            {canDeleteConversation && <Button variant="danger" className="w-full justify-start" onClick={onDeleteConversation}><Trash2 size={15} />Delete this conversation</Button>}
          </div>
        </section>
      </div>

      <footer className="flex shrink-0 justify-end gap-2 border-t border-line bg-panel px-4 py-3 sm:px-6">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button onClick={onSave}>Save changes</Button>
      </footer>
    </section>
  </div>;
}
