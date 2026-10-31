"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Copy, FileText, LoaderCircle, MessageSquarePlus, RotateCcw, Send, Settings2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/queries";
import type { AskResult, ConversationSummary } from "@/types/api";
import { AiSettingsCard, type AiMode, type AiSettingsDraft } from "@/features/dashboard/ai-settings-card";

type Message = { id: string; role: "user" | "assistant"; text: string; sources?: AskResult["sources"]; documentId?: string; documentIds?: string[]; conversationId?: string; requestId?: string; retryQuestion?: string; partial?: boolean; error?: boolean };
type AiPreferences = { mode: AiMode; model: string };
const AI_SETTINGS_KEY = "bankease-ai-settings";
const loanSuggestions = ["Explain this agreement in simple language.", "What interest rate and EMI are stated?", "Which fees should I review?", "Summarize the repayment obligations."];
const policySuggestions = ["What coverage and limits are stated?", "Which exclusions or waiting periods are mentioned?", "When does this policy expire?", "What should I verify before renewal?"];
const financialSuggestions = ["What totals are listed in this statement?", "Do the assets balance with liabilities and equity?", "Which amounts need manual review?", "Explain the source for this total."];
const auditSuggestions = ["What opinion is stated in this report?", "What key audit matters are listed?", "Find any qualified opinion passage.", "What should I read in the full report?"];
const generalSuggestions = ["Explain compound interest in simple terms.", "How does a fixed-rate loan differ from a floating-rate loan?", "What is inflation?", "How can I compare two loan offers?"];
const supportedDocumentTypes = new Set(["loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure", "insurance_policy", "financial_statement", "audit_report", "bank_statement_pdf", "bank_statement_csv", "bank_statement_xlsx"]);

function InlineMarkdown({ text }: { text: string }) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index} className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[.95em]">{part.slice(1, -1)}</code>;
    return <span key={index}>{part}</span>;
  });
}

function AssistantAnswer({ text }: { text: string }) {
  const lines = text.split(/\r?\n/);
  const blocks: Array<{ kind: "paragraph" | "heading" | "unordered" | "ordered"; lines: string[] }> = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index].trim();
    if (!line) { index += 1; continue; }
    const heading = line.match(/^#{1,3}\s+(.+)$/);
    if (heading) { blocks.push({ kind: "heading", lines: [heading[1]] }); index += 1; continue; }
    const unordered = line.match(/^[-*]\s+(.+)$/);
    const ordered = line.match(/^\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const kind = unordered ? "unordered" : "ordered";
      const items = [unordered?.[1] ?? ordered![1]];
      index += 1;
      while (index < lines.length) {
        const next = lines[index].trim();
        const match = kind === "unordered" ? next.match(/^[-*]\s+(.+)$/) : next.match(/^\d+[.)]\s+(.+)$/);
        if (!match) break;
        items.push(match[1]);
        index += 1;
      }
      blocks.push({ kind, lines: items });
      continue;
    }
    blocks.push({ kind: "paragraph", lines: [line] });
    index += 1;
  }
  return <div className="space-y-1.5">{blocks.map((block, index) => block.kind === "heading" ? <h3 key={index} className="font-semibold"><InlineMarkdown text={block.lines[0]} /></h3> : block.kind === "unordered" || block.kind === "ordered" ? block.kind === "unordered" ? <ul key={index} className="list-disc space-y-1 pl-4">{block.lines.map((line, itemIndex) => <li key={itemIndex}><InlineMarkdown text={line} /></li>)}</ul> : <ol key={index} className="list-decimal space-y-1 pl-4">{block.lines.map((line, itemIndex) => <li key={itemIndex}><InlineMarkdown text={line} /></li>)}</ol> : <p key={index}><InlineMarkdown text={block.lines[0]} /></p>)}</div>;
}

export function AssistantPanel({ userName, userId, requestedDocumentId }: { userName: string; userId: string; requestedDocumentId?: string }) {
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents, staleTime: 30_000 });
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: api.conversations, staleTime: 15_000 });
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: api.aiStatus, staleTime: 15_000 });
  const [selection, setSelection] = useState<{ path: string; ids: string[] } | null>(null);
  const [requestedConversationId, setRequestedConversationId] = useState<string | null | undefined>(undefined);
  const [draft, setDraft] = useState("");
  const [savedPreferenceOverride, setSavedPreferenceOverride] = useState<AiPreferences | null>(null);
  const [modeOverride, setModeOverride] = useState<AiMode | null>(null);
  const [modelOverride, setModelOverride] = useState<string | null>(null);
  const [allowHosted, setAllowHosted] = useState(false);
  const [allowHostedDocuments, setAllowHostedDocuments] = useState(false);
  const [webResearch, setWebResearch] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsDraft, setSettingsDraft] = useState<AiSettingsDraft | null>(null);
  const [answerModeOverride, setAnswerModeOverride] = useState<"general" | "selected" | "documents" | null>(null);
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const [copiedId, setCopiedId] = useState("");
  const chatScroll = useRef<HTMLDivElement>(null);
  const shouldStickToBottom = useRef(false);
  const previousConversationId = useRef<string | null>(null);
  const submissionLock = useRef(false);
  const closeSettings = useCallback(() => {
    setSettingsOpen(false);
    setSettingsDraft(null);
  }, []);
  const savedPreferences = useMemo(() => {
    if (!userId || typeof window === "undefined") return null;
    try {
      const saved = localStorage.getItem(`${AI_SETTINGS_KEY}:${userId}`);
      if (!saved) return null;
      const preferences = JSON.parse(saved) as Partial<AiPreferences>;
      if (!["local", "hosted", "auto", "hybrid"].includes(preferences.mode ?? "")) return null;
      return { mode: preferences.mode as AiMode, model: typeof preferences.model === "string" ? preferences.model : null };
    } catch { return null; }
  }, [userId]);
  const preferredMode = savedPreferenceOverride?.mode ?? savedPreferences?.mode ?? null;
  const preferredModel = savedPreferenceOverride?.model ?? savedPreferences?.model ?? null;
  const eligible = (documents.data ?? []).filter((doc) => (supportedDocumentTypes.has(doc.doc_type) || (doc.doc_type === "unknown" && doc.source_format === "docx" && doc.source_locator_type === "section")) && (doc.page_count > 0 || doc.doc_type.startsWith("bank_statement_") || doc.source_locator_type === "section") && ["completed", "needs_review"].includes(doc.status));
  const routeDocumentId = pathname.match(/^\/dashboard\/documents\/([0-9a-f]{32})/)?.[1]
    ?? requestedDocumentId?.toLowerCase().match(/^[0-9a-f]{32}$/)?.[0];
  const selectionKey = `${pathname}:${routeDocumentId ?? ""}`;
  const routeDocument = (documents.data ?? []).find((doc) => doc.id === routeDocumentId);
  const routeDocumentUnsupported = Boolean(routeDocument && !supportedDocumentTypes.has(routeDocument.doc_type) && !(routeDocument.doc_type === "unknown" && routeDocument.source_format === "docx" && routeDocument.source_locator_type === "section"));
  const selectionForPath = selection?.path === selectionKey ? selection.ids : null;
  const defaultConversation = conversations.data?.conversations.find((item) => (item.document_ids ?? (item.document_id ? [item.document_id] : [])).includes(routeDocumentId ?? ""))
    ?? (!routeDocumentId ? conversations.data?.conversations[0] : undefined);
  const activeConversationId = requestedConversationId === undefined ? defaultConversation?.id ?? null : requestedConversationId;
  const activeConversation = conversations.data?.conversations.find((item) => item.id === activeConversationId);
  const activeDocumentIds = activeConversation?.document_ids ?? (activeConversation?.document_id ? [activeConversation.document_id] : []);
  const requestedDocumentIds = selectionForPath ?? (routeDocumentId ? [routeDocumentId] : activeConversation ? activeDocumentIds : []);
  const selectedDocumentIds = requestedDocumentIds.filter((id) => eligible.some((doc) => doc.id === id)).slice(0, 5);
  const selectedId = routeDocumentUnsupported ? "" : selectedDocumentIds[0] ?? "";
  const effectiveDocumentIds = selectedDocumentIds;
  const answerMode = answerModeOverride ?? (effectiveDocumentIds.length === 0 ? "general" : effectiveDocumentIds.length === 1 ? "selected" : "documents");
  const selected = eligible.find((doc) => doc.id === selectedId);
  const suggestions = !selected ? generalSuggestions : selected.doc_type === "insurance_policy" ? policySuggestions : selected.doc_type === "financial_statement" ? financialSuggestions : selected.doc_type === "audit_report" ? auditSuggestions : loanSuggestions;
  const sameDocuments = (left: string[], right: string[]) => left.length === right.length && left.every((id) => right.includes(id));
  const selectedConversation = activeConversation && sameDocuments(activeDocumentIds, effectiveDocumentIds) ? activeConversation : undefined;
  const mode = modeOverride ?? (selectedConversation?.mode as AiMode | undefined) ?? preferredMode ?? aiStatus.data?.default_mode ?? "local";
  const installedModelNames = aiStatus.data?.local.models.map((item) => item.name) ?? [];
  const preferredInstalledModel = preferredModel && installedModelNames.includes(preferredModel) ? preferredModel : null;
  const configuredInstalledModel = aiStatus.data?.local.model_available ? aiStatus.data.local.configured_model : null;
  const requestedModel = modelOverride ?? preferredInstalledModel ?? configuredInstalledModel ?? installedModelNames[0] ?? aiStatus.data?.local.configured_model ?? "";
  const requestedLocalModelAvailable = aiStatus.data?.local.models.some((item) => item.name === requestedModel) ?? false;
  const messagesQuery = useQuery({
    queryKey: ["conversation-messages", selectedConversation?.id],
    queryFn: () => api.conversationMessages(selectedConversation!.id),
    enabled: Boolean(selectedConversation),
  });
  const messages: Message[] = [
    ...(messagesQuery.data?.messages ?? []).map((message) => ({
      id: message.id, role: message.role, text: message.text, sources: message.sources,
      documentId: selectedConversation?.document_id ?? undefined,
      documentIds: selectedConversation?.document_ids ?? [],
    })),
    ...localMessages.filter((message) => sameDocuments(message.documentIds ?? [message.documentId ?? ""].filter(Boolean), effectiveDocumentIds)),
  ];
  const lastMessageText = messages[messages.length - 1]?.text ?? "";
  useEffect(() => {
    if (previousConversationId.current !== activeConversationId) {
      previousConversationId.current = activeConversationId;
      shouldStickToBottom.current = Boolean(activeConversationId);
    }
    const conversation = chatScroll.current;
    if (conversation && shouldStickToBottom.current) conversation.scrollTo({ top: conversation.scrollHeight, behavior: "smooth" });
  }, [messages.length, lastMessageText, activeConversationId]);
  const answer = useMutation({
    mutationFn: async ({ question, documentIds, conversationId, requestId, options }: { question: string; documentIds: string[]; conversationId: string | null; requestId: string; options: { mode: AiMode; model?: string; allow_hosted: boolean; allow_hosted_documents: boolean; web_research: boolean } }) => {
      const conversation = conversationId ? null : await api.createConversation(documentIds, question, options.mode);
      const id = conversationId ?? conversation!.id;
      if (conversation) {
        setRequestedConversationId(id);
        queryClient.setQueryData<Awaited<ReturnType<typeof api.conversations>>>(["conversations"], (current) => ({
          conversations: [conversation, ...(current?.conversations ?? []).filter((item) => item.id !== conversation.id)].slice(0, 100),
        }));
      }
      try {
        const sent = await api.sendConversationMessageStream(id, question, requestId, options, (text) => {
          setLocalMessages((items) => {
            const current = items.find((item) => item.id === "streaming-response");
            const next: Message = { id: "streaming-response", role: "assistant", text: `${current?.text ?? ""}${text}`, documentId: documentIds[0], documentIds };
            return current ? items.map((item) => item.id === "streaming-response" ? next : item) : [...items, next];
          });
        });
        return { conversationId: id, conversation, sent };
      } catch (error) {
        const wrapped = new Error(error instanceof Error ? error.message : "The chat request failed.") as Error & { conversationId?: string };
        wrapped.name = error instanceof Error ? error.name : "Error";
        wrapped.conversationId = id;
        throw wrapped;
      }
    },
    onSuccess: ({ conversationId, conversation, sent }) => {
      submissionLock.current = false;
      setRequestedConversationId(conversationId);
      setLocalMessages([]);
      if (conversation) {
        queryClient.setQueryData<Awaited<ReturnType<typeof api.conversations>>>(["conversations"], (current) => ({
          conversations: [conversation, ...(current?.conversations ?? []).filter((item) => item.id !== conversation.id)].slice(0, 100),
        }));
      }
      const key = ["conversation-messages", conversationId];
      const previous = queryClient.getQueryData<Awaited<ReturnType<typeof api.conversationMessages>>>(key);
      const summary = conversation ?? previous?.conversation ?? conversations.data?.conversations.find((item) => item.id === conversationId);
      if (summary) queryClient.setQueryData(key, {
        conversation: summary,
        messages: [...(previous?.messages ?? []), ...sent.messages],
        offset: 0, limit: 100, total: (previous?.total ?? 0) + sent.messages.length,
      });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["conversation-messages", conversationId] });
    },
    onError: (error, variables) => {
      submissionLock.current = false;
      const conversationId = (error as Error & { conversationId?: string }).conversationId ?? variables.conversationId ?? undefined;
      if (conversationId) setRequestedConversationId(conversationId);
      setLocalMessages((items) => {
        const partial = items.find((item) => item.id === "streaming-response")?.text;
        const retained = items.filter((item) => item.id !== "streaming-response");
        return [...retained, ...(partial ? [{ id: crypto.randomUUID(), role: "assistant" as const, text: partial, documentId: variables.documentIds[0], documentIds: variables.documentIds, partial: true }] : []), {
          id: crypto.randomUUID(), role: "assistant", text: error.message,
          documentId: variables.documentIds[0], documentIds: variables.documentIds,
          conversationId, requestId: variables.requestId, retryQuestion: variables.question, error: true,
        }];
      });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const deleteChat = useMutation({
    mutationFn: (id: string) => api.deleteConversation(id),
    onSuccess: async (_, id) => {
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.removeQueries({ queryKey: ["conversation-messages", id] });
      setRequestedConversationId(null);
      setLocalMessages([]);
      closeSettings();
    },
    onError: (error) => toast.error(error.message),
  });

  function submit(question: string) {
    const trimmed = question.trim();
    if (!trimmed || answer.isPending || submissionLock.current) return;
    submissionLock.current = true;
    shouldStickToBottom.current = true;
    const requestId = crypto.randomUUID().replaceAll("-", "");
    setLocalMessages((items) => [...items, { id: crypto.randomUUID(), role: "user", text: trimmed, documentId: selectedId || undefined, documentIds: effectiveDocumentIds, requestId }]);
    setDraft("");
    setSelection({ path: selectionKey, ids: effectiveDocumentIds });
    answer.mutate({ question: trimmed, documentIds: effectiveDocumentIds, requestId,
      conversationId: selectedConversation ? activeConversationId : null,
      options: { mode, ...(mode === "hosted" ? {} : mode === "hybrid" && !effectiveDocumentIds.length ? {} : (mode === "auto" && !requestedLocalModelAvailable ? {} : { model: requestedModel || undefined })), allow_hosted: allowHosted, allow_hosted_documents: allowHostedDocuments, web_research: webResearch } });
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit(draft);
  }

  function newConversation() {
    if (answer.isPending || submissionLock.current) return;
    answer.reset();
    setLocalMessages([]);
    setRequestedConversationId(null);
    setModeOverride(null);
    setModelOverride(null);
    setAllowHosted(false);
    setAllowHostedDocuments(false);
    setWebResearch(false);
    setAnswerModeOverride(null);
  }

  function chooseConversation(id: string) {
    answer.reset();
    const next = conversations.data?.conversations.find((item: ConversationSummary) => item.id === id);
    if (id && next) setSelection({ path: selectionKey, ids: next.document_ids ?? (next.document_id ? [next.document_id] : []) });
    setModeOverride(next ? (next.mode as AiMode) : null);
    setModelOverride(null);
    setAllowHosted(false);
    setAllowHostedDocuments(false);
    setWebResearch(false);
    setLocalMessages([]);
    setRequestedConversationId(id || null);
    setAnswerModeOverride(null);
  }

  function clearConversation() {
    if (!selectedConversation || !window.confirm("Delete this conversation and its saved questions and answers?")) return;
    deleteChat.mutate(selectedConversation.id);
  }

  function retry(message: Message) {
    if (!message.retryQuestion || answer.isPending || submissionLock.current) return;
    submissionLock.current = true;
    shouldStickToBottom.current = true;
    setLocalMessages((items) => items.filter((item) => item.id !== message.id));
    const documentIds = message.documentIds ?? (message.documentId ? [message.documentId] : []);
    const conversationId = message.conversationId ?? (selectedConversation && sameDocuments(activeDocumentIds, documentIds) ? activeConversationId : null);
    setSelection({ path: selectionKey, ids: documentIds });
    if (!conversationId) setRequestedConversationId(null);
    answer.mutate({ question: message.retryQuestion, documentIds, conversationId, requestId: message.requestId ?? crypto.randomUUID().replaceAll("-", ""),
      options: { mode, ...(mode === "hosted" ? {} : mode === "hybrid" && !documentIds.length ? {} : (mode === "auto" && !requestedLocalModelAvailable ? {} : { model: requestedModel || undefined })), allow_hosted: allowHosted, allow_hosted_documents: allowHostedDocuments, web_research: webResearch } });
  }

  function updateSelection(ids: string[]) {
    const validIds = [...new Set(ids.filter((id) => eligible.some((doc) => doc.id === id)))].slice(0, 5);
    setSelection({ path: selectionKey, ids: validIds });
    setRequestedConversationId(null);
    setLocalMessages([]);
    answer.reset();
  }

  function toggleAdditionalDocument(documentId: string, checked: boolean) {
    if (checked && effectiveDocumentIds.length >= 5) {
      toast.error("Choose up to five documents for one question.");
      return;
    }
    updateSelection(checked
      ? [...effectiveDocumentIds, documentId]
      : effectiveDocumentIds.filter((id) => id !== documentId));
  }

  async function copy(message: Message) {
    try {
      await navigator.clipboard.writeText(message.text);
      setCopiedId(message.id);
      window.setTimeout(() => setCopiedId(""), 1300);
    } catch { /* Clipboard can be unavailable in non-secure local contexts. */ }
  }

  function openSettings() {
    setSettingsDraft({ mode, model: requestedModel, allowHosted, allowHostedDocuments, webResearch });
    setSettingsOpen(true);
  }

  function saveSettings() {
    if (!settingsDraft) return;
    setModeOverride(settingsDraft.mode);
    setModelOverride(settingsDraft.model);
    setAllowHosted(settingsDraft.allowHosted);
    setAllowHostedDocuments(settingsDraft.allowHostedDocuments);
    setWebResearch(settingsDraft.webResearch);
    setSavedPreferenceOverride({ mode: settingsDraft.mode, model: settingsDraft.model });
    try {
      localStorage.setItem(AI_SETTINGS_KEY + ":" + userId, JSON.stringify({ mode: settingsDraft.mode, model: settingsDraft.model } satisfies AiPreferences));
    } catch { toast.error("Settings are active for this session but could not be saved on this device."); }
    closeSettings();
  }

  function chooseSettingsConversation(id: string) {
    chooseConversation(id);
    const next = conversations.data?.conversations.find((item) => item.id === id);
    setSettingsDraft((current) => current ? {
      ...current,
      mode: next ? (next.mode as AiMode) : preferredMode ?? aiStatus.data?.default_mode ?? "local",
    } : current);
  }

  return <>
    <section aria-label="AI assistant" className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-2xl border border-line bg-panel shadow-lg shadow-slate-950/5">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-3.5 sm:px-5">
        <div className="min-w-0">
          <h1 className="text-base font-semibold tracking-tight text-ink sm:text-lg">AI Assistant</h1>
          <p className="mt-0.5 truncate text-xs text-muted">Ask questions about your financial documents.</p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button variant="ghost" size="icon" onClick={openSettings} aria-label="Open AI settings" title="AI settings"><Settings2 size={17} /></Button>
          <Button variant="ghost" size="icon" onClick={newConversation} disabled={answer.isPending} aria-label="Start a new conversation" title="New conversation"><MessageSquarePlus size={17} /></Button>
        </div>
      </header>

      <div ref={chatScroll} onScroll={(event) => { const element = event.currentTarget; shouldStickToBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96; }} className="min-h-0 min-w-0 flex-1 space-y-4 overflow-y-auto overscroll-contain bg-app/40 p-3.5 sm:p-5" aria-live="polite">
        {messagesQuery.isError && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">Could not load this conversation. Refresh your documents and try again.</p>}
        {messagesQuery.isLoading && <p className="flex items-center gap-2 text-xs text-muted"><LoaderCircle size={14} className="animate-spin" />Loading saved conversation...</p>}
        {documents.isError && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">Could not load your document list. You can still ask a general question.</p>}
        {messages.length === 0 && <div className="mx-auto w-full max-w-2xl rounded-2xl border border-line bg-panel p-4 shadow-sm sm:p-5">
          <p className="text-sm font-semibold text-ink">Hi {userName.split(" ")[0] || "there"}!</p>
          <p className="mt-2 text-xs leading-5 text-muted">{selected ? selected.source_locator_type === "section" ? "I search this Word document's extracted text and cite section numbers because page layout is unavailable." : "I answer using extracted document passages and cite source pages or statement rows." : "Ask a general question, or choose a document in AI settings for an evidence-backed answer."} I cannot access bank accounts or make payments.</p>
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {suggestions.map((prompt) => <button type="button" key={prompt} onClick={() => submit(prompt)} className="min-h-10 rounded-lg border border-line bg-panel-raised px-3 py-2 text-left text-xs leading-5 text-ink transition hover:border-blue-300 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50">{prompt}</button>)}
          </div>
        </div>}
        {messages.map((message) => <article key={message.id} className={"min-w-0 max-w-[95%] [overflow-wrap:anywhere] rounded-2xl border px-4 py-3 text-xs leading-5 shadow-sm " + (message.role === "user" ? "ml-auto border-blue-600 bg-blue-600 text-white" : message.error ? "border-red-200 bg-red-50 text-red-800" : "border-line bg-panel text-ink")}>
          <div className="mb-1.5 flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-wide opacity-75"><Bot size={12} />{message.role === "user" ? "You" : "BankEase AI"}</div>
          {message.partial && <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-amber-700">Partial response — not saved</p>}
          {message.error ? <p>{message.text}</p> : <AssistantAnswer text={message.text} />}
          {message.role === "assistant" && message.sources && message.sources.length > 0 && <div className="mt-3 space-y-2 border-t border-line pt-2">
            <p className="text-[9px] font-bold uppercase tracking-wide text-muted">Sources</p>
            {message.sources.map((source, index) => <div key={(source.citation_id ?? source.document_id ?? message.documentId ?? "source") + "-" + source.page + "-" + index} className="rounded-lg bg-panel-raised p-2.5">
              <p className="text-[10px] font-semibold text-blue-700">{source.kind === "web" ? "Web · " + (source.title ?? "Source") : (source.filename ?? (source.document_id === selectedId ? selected?.filename : "Selected document")) + " | " + (source.location ?? (source.page ? "Page " + source.page : source.source_sheet ? source.source_sheet + ", row " + (source.row_number ?? "?") : source.row_number ? "Row " + source.row_number : source.kind === "calculation" ? "Calculated from imported rows" : "Source"))}</p>
              <p className="mt-1 [overflow-wrap:anywhere] text-[10px] leading-4 text-muted">{source.text}</p>
              {source.document_id && <Link className="mt-1 inline-flex items-center gap-1 text-[9px] font-semibold text-blue-700 hover:underline" href={"/dashboard/documents/" + source.document_id}><FileText size={10} />Open cited document</Link>}
              {source.url && <a className="mt-1 inline-flex items-center gap-1 text-[9px] font-semibold text-blue-700 hover:underline" href={source.url} target="_blank" rel="noreferrer">Open web source</a>}
            </div>)}
          </div>}
          {message.role === "assistant" && <div className="mt-2 flex flex-wrap justify-end gap-1">
            {message.error && message.retryQuestion && <button type="button" onClick={() => retry(message)} disabled={answer.isPending} className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[9px] text-red-700 hover:bg-red-100 disabled:opacity-50"><RotateCcw size={11} />Retry</button>}
            <button type="button" onClick={() => void copy(message)} className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[9px] text-muted hover:bg-panel-raised hover:text-ink" aria-label="Copy assistant response">{copiedId === message.id ? <Check size={11} /> : <Copy size={11} />}{copiedId === message.id ? "Copied" : "Copy"}</button>
            {message.documentId && <Link className="ml-1 inline-flex items-center gap-1 rounded px-1.5 py-1 text-[9px] text-blue-700 hover:bg-blue-50" href={"/dashboard/documents/" + message.documentId}><FileText size={11} />Open source</Link>}
          </div>}
        </article>)}
        {answer.isPending && <div className="flex w-fit items-center gap-2 rounded-xl border border-line bg-panel px-3 py-2 text-xs text-muted"><LoaderCircle size={14} className="animate-spin" />Generating response...</div>}
        {messages.length > 0 && !answer.isPending && <div className="flex flex-wrap gap-1.5">{["What should I verify?", "Explain this more simply", "Which page supports that?"].map((prompt) => <button key={prompt} type="button" onClick={() => submit(prompt)} className="rounded-full border border-line bg-panel px-3 py-1.5 text-[10px] text-muted hover:border-blue-300 hover:text-ink disabled:opacity-50">{prompt}</button>)}</div>}
      </div>

      <form onSubmit={onSubmit} className="shrink-0 border-t border-line bg-panel p-3 sm:p-4">
        <div className="flex items-end gap-2">
          <textarea value={draft} onChange={(event) => setDraft(event.target.value.slice(0, 500))} rows={2} maxLength={500} className="min-h-10 min-w-0 flex-1 resize-none rounded-xl border border-line bg-panel-raised px-3 py-2.5 text-xs text-ink placeholder:text-muted outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20" placeholder="Ask a question about anything..." aria-label="Ask BankEase AI a question" onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(draft); } }} />
          <Button type="submit" className="shrink-0" size="icon" aria-label="Send question" disabled={!draft.trim() || answer.isPending}><Send size={15} /></Button>
        </div>
        <p className="mt-1.5 text-[9px] text-muted">Answers may be incomplete. Check important details against the cited page.</p>
      </form>
    </section>

    <AiSettingsCard
      open={settingsOpen}
      draft={settingsDraft ?? { mode, model: requestedModel, allowHosted, allowHostedDocuments, webResearch }}
      aiStatus={aiStatus.data}
      aiStatusLoading={aiStatus.isLoading}
      aiStatusError={aiStatus.isError}
      conversations={conversations.data?.conversations ?? []}
      selectedConversationId={activeConversationId ?? ""}
      onChooseConversation={chooseSettingsConversation}
      documents={eligible}
      answerMode={answerMode}
      selectedDocumentId={selectedId}
      selectedDocumentIds={effectiveDocumentIds}
      onAnswerModeChange={(answerMode) => {
        setAnswerModeOverride(answerMode);
        if (answerMode === "general") updateSelection([]);
        else if (answerMode === "selected") {
          const documentId = selectedId || effectiveDocumentIds[0] || eligible[0]?.id;
          if (documentId) updateSelection([documentId]);
        } else if (effectiveDocumentIds.length === 0 && eligible[0]) updateSelection([eligible[0].id]);
      }}
      onSelectDocument={(id) => {
        setAnswerModeOverride(effectiveDocumentIds.length > 1 ? "documents" : "selected");
        updateSelection([id, ...effectiveDocumentIds.filter((item) => item !== id)]);
      }}
      onToggleAdditionalDocument={(id, checked) => {
        setAnswerModeOverride(checked || effectiveDocumentIds.length > 2 ? "documents" : "selected");
        toggleAdditionalDocument(id, checked);
      }}
      onDraftChange={(change) => setSettingsDraft((current) => ({ ...(current ?? { mode, model: requestedModel, allowHosted, allowHostedDocuments, webResearch }), ...change }))}
      onDeleteConversation={clearConversation}
      canDeleteConversation={Boolean(selectedConversation)}
      onClose={closeSettings}
      onSave={saveSettings}
    />
  </>;
}
