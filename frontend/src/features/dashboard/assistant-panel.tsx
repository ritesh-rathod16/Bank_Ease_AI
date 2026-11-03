"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Copy, FileText, LoaderCircle, MessageSquarePlus, Paperclip, PanelLeftClose, PanelLeftOpen, Pencil, Pin, PinOff, RotateCcw, Search, Send, Settings2, Square, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { generateConversationTitle } from "@/lib/conversation-title";
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
const automaticDocumentQuestion = "Analyze the attached document(s). Summarize the key information, important terms, amounts, dates, obligations, notable risks, missing information, and any details that may need review. For bank statements, summarize actual income, spending, and balance checks from imported transactions. Cite the supporting pages, sections, or statement rows.";
const supportedDocumentTypes = new Set(["loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure", "insurance_policy", "financial_statement", "audit_report", "bank_statement_pdf", "bank_statement_csv", "bank_statement_xlsx"]);
const acceptedUploadExtensions = new Set(["pdf", "docx", "txt", "csv", "xlsx", "json", "jsonl", "png", "jpg", "jpeg", "tif", "tiff"]);

type AnswerBlock =
  | { kind: "paragraph" | "heading"; lines: string[] }
  | { kind: "unordered" | "ordered"; lines: string[] }
  | { kind: "code"; lines: string[]; language: string }
  | { kind: "table"; rows: string[][] };

function InlineMarkdown({ text }: { text: string }) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index} className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[.95em]">{part.slice(1, -1)}</code>;
    return <span key={index}>{part}</span>;
  });
}

function AssistantAnswer({ text }: { text: string }) {
  const lines = text.split(/\r?\n/);
  const blocks: AnswerBlock[] = [];
  const cells = (line: string) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  for (let index = 0; index < lines.length;) {
    const line = lines[index].trim();
    if (!line) { index += 1; continue; }
    const code = line.match(/^```\s*([\w+-]*)$/);
    if (code) {
      const content: string[] = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) content.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push({ kind: "code", lines: content, language: code[1] });
      continue;
    }
    if (line.includes("|") && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) {
      const rows = [cells(line)];
      index += 2;
      while (index < lines.length && lines[index].includes("|")) rows.push(cells(lines[index++]));
      blocks.push({ kind: "table", rows });
      continue;
    }
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
  return <div className="space-y-2">{blocks.map((block, index) => {
    if (block.kind === "heading") return <h3 key={index} className="font-semibold"><InlineMarkdown text={block.lines[0]} /></h3>;
    if (block.kind === "unordered") return <ul key={index} className="list-disc space-y-1 pl-4">{block.lines.map((line, itemIndex) => <li key={itemIndex}><InlineMarkdown text={line} /></li>)}</ul>;
    if (block.kind === "ordered") return <ol key={index} className="list-decimal space-y-1 pl-4">{block.lines.map((line, itemIndex) => <li key={itemIndex}><InlineMarkdown text={line} /></li>)}</ol>;
    if (block.kind === "code") return <pre key={index} className="max-w-full overflow-x-auto rounded-lg border border-line bg-slate-950 p-3 text-[10px] leading-4 text-slate-100"><code>{block.lines.join("\n")}</code></pre>;
    if (block.kind === "table") return <div key={index} className="max-w-full overflow-x-auto rounded-lg border border-line"><table className="w-full border-collapse text-left text-[10px]"><thead className="bg-panel-raised"><tr>{block.rows[0].map((cell, cellIndex) => <th key={cellIndex} className="border-b border-line px-2 py-1.5 font-semibold"><InlineMarkdown text={cell} /></th>)}</tr></thead><tbody>{block.rows.slice(1).map((row, rowIndex) => <tr key={rowIndex} className="odd:bg-panel even:bg-panel-raised/40">{block.rows[0].map((_, cellIndex) => <td key={cellIndex} className="border-b border-line px-2 py-1.5 align-top">{row[cellIndex] ? <InlineMarkdown text={row[cellIndex]} /> : null}</td>)}</tr>)}</tbody></table></div>;
    return <p key={index}><InlineMarkdown text={block.lines[0]} /></p>;
  })}</div>;
}

export function AssistantPanel({ userId, requestedDocumentId, requestedQuestion }: { userName: string; userId: string; requestedDocumentId?: string; requestedQuestion?: string }) {
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents, staleTime: 30_000 });
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: api.conversations, staleTime: 15_000 });
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: api.aiStatus, staleTime: 15_000 });
  const [selection, setSelection] = useState<{ path: string; ids: string[] } | null>(null);
  const [requestedConversationId, setRequestedConversationId] = useState<string | null | undefined>(undefined);
  const [draft, setDraft] = useState("");
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [uploadStatus, setUploadStatus] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileHistoryOpen, setMobileHistoryOpen] = useState(false);
  const [historySearch, setHistorySearch] = useState("");
  const [renamingConversationId, setRenamingConversationId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
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
  const uploadInput = useRef<HTMLInputElement>(null);
  const shouldStickToBottom = useRef(false);
  const previousConversationId = useRef<string | null>(null);
  const submissionLock = useRef(false);
  const abortController = useRef<AbortController | null>(null);
  const autoQuestionSubmitted = useRef<string | null>(null);
  const submitQuestionRef = useRef<(question: string) => void>(() => undefined);
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
  const eligible = (documents.data ?? []).filter((doc) => (supportedDocumentTypes.has(doc.doc_type) || (doc.doc_type === "unknown" && doc.source_locator_type === "section")) && (doc.page_count > 0 || doc.doc_type.startsWith("bank_statement_") || doc.source_locator_type === "section") && ["completed", "needs_review"].includes(doc.status));
  const routeDocumentId = pathname.match(/^\/dashboard\/documents\/([0-9a-f]{32})/)?.[1]
    ?? requestedDocumentId?.toLowerCase().match(/^[0-9a-f]{32}$/)?.[0];
  const selectionKey = `${pathname}:${routeDocumentId ?? ""}`;
  const routeDocument = (documents.data ?? []).find((doc) => doc.id === routeDocumentId);
  const routeDocumentUnsupported = Boolean(routeDocument && !supportedDocumentTypes.has(routeDocument.doc_type) && !(routeDocument.doc_type === "unknown" && routeDocument.source_locator_type === "section"));
  const selectionForPath = selection?.path === selectionKey ? selection.ids : null;
  const orderedConversations = [...(conversations.data?.conversations ?? [])].sort((left, right) => Number(right.is_pinned) - Number(left.is_pinned) || right.updated_at.localeCompare(left.updated_at));
  const filteredConversations = orderedConversations.filter((item) => `${item.title} ${item.document_filenames?.join(" ") ?? item.document_filename}`.toLowerCase().includes(historySearch.trim().toLowerCase()));
  const defaultConversation = orderedConversations.find((item) => (item.document_ids ?? (item.document_id ? [item.document_id] : [])).includes(routeDocumentId ?? ""))
    ?? (!routeDocumentId ? orderedConversations[0] : undefined);
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
    mutationFn: async (variables: { question: string; documentIds: string[]; files: File[]; conversationId: string | null; requestId: string; options: { mode: AiMode; model?: string; allow_hosted: boolean; allow_hosted_documents: boolean; web_research: boolean } }) => {
      let documentIds = [...variables.documentIds];
      let conversation = null as ConversationSummary | null;
      let id = variables.conversationId;
      try {
        if (variables.files.length) {
          const newDocumentIds: string[] = [];
          for (let index = 0; index < variables.files.length; index += 1) {
            const file = variables.files[index];
            setUploadStatus(`Uploading ${file.name} (${index + 1}/${variables.files.length})`);
            const uploaded = await api.upload(file);
            newDocumentIds.push(uploaded.id);
            setUploadStatus(`Analyzing ${file.name}…`);
            let status = await api.documentStatus(uploaded.id);
            for (let attempt = 0; status.status === "processing" && attempt < 180; attempt += 1) {
              await new Promise((resolve) => window.setTimeout(resolve, 1_000));
              status = await api.documentStatus(uploaded.id);
            }
            if (status.status === "failed") throw new Error(status.error || `Could not analyze ${file.name}.`);
            if (!["completed", "needs_review"].includes(status.status)) throw new Error(`Analysis of ${file.name} is taking too long. It remains in your document library; try sending it again after processing finishes.`);
          }
          documentIds = [...new Set([...documentIds, ...newDocumentIds])].slice(0, 5);
          variables.documentIds = documentIds;
          setAttachedFiles([]);
          setSelection({ path: selectionKey, ids: documentIds });
          setLocalMessages((items) => items.map((item) => item.requestId === variables.requestId ? { ...item, documentId: documentIds[0], documentIds } : item));
          void queryClient.invalidateQueries({ queryKey: ["documents"] });
        }
        if (id && variables.files.length) {
          conversation = await api.updateConversation(id, { document_ids: documentIds });
          queryClient.setQueryData<Awaited<ReturnType<typeof api.conversations>>>(["conversations"], (current) => ({
            conversations: [conversation!, ...(current?.conversations ?? []).filter((item) => item.id !== conversation!.id)].slice(0, 100),
          }));
        } else if (!id) {
          conversation = await api.createConversation(documentIds, generateConversationTitle(variables.question), variables.options.mode);
          id = conversation.id;
          setRequestedConversationId(id);
          queryClient.setQueryData<Awaited<ReturnType<typeof api.conversations>>>(["conversations"], (current) => ({
            conversations: [conversation!, ...(current?.conversations ?? []).filter((item) => item.id !== conversation!.id)].slice(0, 100),
          }));
        }
        if (!id) throw new Error("Could not create the conversation.");
        const controller = new AbortController();
        abortController.current = controller;
        setUploadStatus("streaming");
        const sent = await api.sendConversationMessageStream(id, variables.question, variables.requestId, variables.options, (text) => {
          setLocalMessages((items) => {
            const current = items.find((item) => item.id === "streaming-response");
            const next: Message = { id: "streaming-response", role: "assistant", text: `${current?.text ?? ""}${text}`, documentId: documentIds[0], documentIds };
            return current ? items.map((item) => item.id === "streaming-response" ? next : item) : [...items, next];
          });
        }, controller.signal);
        return { conversationId: id, conversation, sent };
      } catch (error) {
        const wrapped = new Error(error instanceof Error ? error.message : "The chat request failed.") as Error & { conversationId?: string; status?: number };
        wrapped.name = error instanceof Error ? error.name : "Error";
        wrapped.conversationId = id ?? undefined;
        wrapped.status = (error as Error & { status?: number })?.status;
        throw wrapped;
      } finally {
        abortController.current = null;
        setUploadStatus("");
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
      const cancelled = (error as Error & { status?: number }).status === 499;
      setLocalMessages((items) => {
        const partial = items.find((item) => item.id === "streaming-response")?.text;
        const retained = items.filter((item) => item.id !== "streaming-response");
        if (cancelled) return [...retained, ...(partial ? [{ id: crypto.randomUUID(), role: "assistant" as const, text: partial, documentId: variables.documentIds[0], documentIds: variables.documentIds, partial: true }] : [])];
        return [...retained, ...(partial ? [{ id: crypto.randomUUID(), role: "assistant" as const, text: partial, documentId: variables.documentIds[0], documentIds: variables.documentIds, partial: true }] : []), {
          id: crypto.randomUUID(), role: "assistant", text: error.message,
          documentId: variables.documentIds[0], documentIds: variables.documentIds,
          conversationId, requestId: variables.requestId, retryQuestion: variables.question, error: true,
        }];
      });
      if (cancelled) toast.info("Generation stopped. This partial response was not saved.");
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const deleteChat = useMutation({
    mutationFn: (id: string) => api.deleteConversation(id),
    onSuccess: async (_, id) => {
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.removeQueries({ queryKey: ["conversation-messages", id] });
      if (activeConversationId === id) {
        setRequestedConversationId(null);
        setLocalMessages([]);
        closeSettings();
      }
    },
    onError: (error) => toast.error(error.message),
  });

  const updateChat = useMutation({
    mutationFn: ({ id, change }: { id: string; change: { title?: string; is_pinned?: boolean } }) => api.updateConversation(id, change),
    onSuccess: (updated) => queryClient.setQueryData<Awaited<ReturnType<typeof api.conversations>>>(["conversations"], (current) => ({
      conversations: (current?.conversations ?? []).map((item) => item.id === updated.id ? updated : item),
    })),
    onError: (error) => toast.error(error.message),
  });

  function addFiles(fileList: FileList | File[]) {
    const candidates = Array.from(fileList);
    const valid = candidates.filter((file) => acceptedUploadExtensions.has(file.name.split(".").pop()?.toLowerCase() ?? ""));
    if (valid.length !== candidates.length) toast.error("Choose PDF, DOCX, TXT, CSV, XLSX, JSON, JSONL, PNG, JPEG, or TIFF files.");
    const remaining = Math.max(0, 5 - effectiveDocumentIds.length - attachedFiles.length);
    if (valid.length > remaining) toast.error("A conversation can contain up to five documents.");
    if (remaining > 0 && valid.length) setAttachedFiles((current) => [...current, ...valid.slice(0, remaining).filter((file) => !current.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified))]);
  }

  function onDragOver(event: DragEvent<HTMLElement>) {
    if (event.dataTransfer.types.includes("Files")) {
      event.preventDefault();
      setDragActive(true);
    }
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragActive(false);
    addFiles(event.dataTransfer.files);
  }

  function submit(question: string, files = attachedFiles) {
    const typed = question.trim();
    const trimmed = typed || (files.length ? automaticDocumentQuestion : "");
    if (!trimmed || answer.isPending || submissionLock.current) return;
    submissionLock.current = true;
    shouldStickToBottom.current = true;
    const requestId = crypto.randomUUID().replaceAll("-", "");
    setLocalMessages((items) => [...items, { id: crypto.randomUUID(), role: "user", text: trimmed, documentId: selectedId || undefined, documentIds: effectiveDocumentIds, requestId }]);
    setDraft("");
    setSelection({ path: selectionKey, ids: effectiveDocumentIds });
    answer.mutate({ question: trimmed, documentIds: effectiveDocumentIds, files: [...files], requestId,
      conversationId: selectedConversation ? activeConversationId : null,
      options: { mode, ...(mode === "hosted" ? {} : mode === "hybrid" && !effectiveDocumentIds.length ? {} : (mode === "auto" && !requestedLocalModelAvailable ? {} : { model: requestedModel || undefined })), allow_hosted: allowHosted, allow_hosted_documents: allowHostedDocuments, web_research: webResearch } });
  }
  useEffect(() => { submitQuestionRef.current = submit; });

  const initialQuestion = requestedQuestion?.trim().slice(0, 500) ?? "";
  const initialQuestionKey = initialQuestion && routeDocumentId ? `${routeDocumentId}:${initialQuestion}` : "";
  useEffect(() => {
    if (!initialQuestionKey || !documents.isSuccess || selectedId !== routeDocumentId || answer.isPending || submissionLock.current || autoQuestionSubmitted.current === initialQuestionKey) return;
    autoQuestionSubmitted.current = initialQuestionKey;
    submitQuestionRef.current(initialQuestion);
  }, [answer.isPending, documents.isSuccess, initialQuestion, initialQuestionKey, routeDocumentId, selectedId]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit(draft);
  }

  function newConversation() {
    if (answer.isPending || submissionLock.current) return;
    setMobileHistoryOpen(false);
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
    setMobileHistoryOpen(false);
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
    answer.mutate({ question: message.retryQuestion, documentIds, files: [], conversationId, requestId: message.requestId ?? crypto.randomUUID().replaceAll("-", ""),
      options: { mode, ...(mode === "hosted" ? {} : mode === "hybrid" && !documentIds.length ? {} : (mode === "auto" && !requestedLocalModelAvailable ? {} : { model: requestedModel || undefined })), allow_hosted: allowHosted, allow_hosted_documents: allowHostedDocuments, web_research: webResearch } });
  }

  function stopGeneration() {
    if (uploadStatus === "streaming") abortController.current?.abort();
  }

  function startRename(item: ConversationSummary) {
    setRenamingConversationId(item.id);
    setRenameDraft(item.title);
  }

  function saveRename(event: FormEvent<HTMLFormElement>, id: string) {
    event.preventDefault();
    const title = renameDraft.trim();
    if (!title) { toast.error("Enter a conversation name."); return; }
    updateChat.mutate({ id, change: { title } });
    setRenamingConversationId(null);
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
    <div className="relative flex h-full min-h-0 min-w-0 overflow-hidden rounded-[26px] border border-slate-200 bg-white shadow-[0_12px_32px_rgba(15,23,42,0.06)]" onDragOver={onDragOver} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragActive(false); }} onDrop={onDrop}>
      {mobileHistoryOpen && <button type="button" aria-label="Close conversation history" onClick={() => setMobileHistoryOpen(false)} className="fixed inset-0 z-40 bg-slate-950/50 md:hidden" />}
      <aside aria-label="Conversation history" className={(mobileHistoryOpen ? `fixed inset-y-0 left-0 z-50 flex w-72 flex-col shadow-2xl md:relative md:inset-auto md:z-auto md:shadow-none ${sidebarCollapsed ? "md:w-14" : "md:w-64"}` : `hidden md:flex md:relative md:z-auto ${sidebarCollapsed ? "md:w-14" : "md:w-64"}`) + " shrink-0 border-r border-slate-200 bg-slate-50/70 transition-[width]"}>
        <div className="flex shrink-0 items-center justify-between gap-1 border-b border-slate-200 bg-white/80 p-2.5 backdrop-blur-sm">
          {(!sidebarCollapsed || mobileHistoryOpen) && <span className="px-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Chats</span>}
          <div className="ml-auto flex items-center gap-1">
            <Button variant="ghost" size="icon" onClick={newConversation} disabled={answer.isPending} aria-label="New chat" title="New chat" className="rounded-lg hover:bg-slate-100"><MessageSquarePlus size={15} /></Button>
            <Button variant="ghost" size="icon" onClick={() => mobileHistoryOpen ? setMobileHistoryOpen(false) : setSidebarCollapsed((value) => !value)} aria-label={sidebarCollapsed ? "Expand conversation history" : "Collapse conversation history"} title="Collapse sidebar" className="rounded-lg hover:bg-slate-100">{sidebarCollapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}</Button>
          </div>
        </div>
        {(!sidebarCollapsed || mobileHistoryOpen) && <>
          <div className="shrink-0 border-b border-slate-200 bg-white/70 p-2.5">
            <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-2 text-slate-500"><Search size={13} /><input value={historySearch} onChange={(event) => setHistorySearch(event.target.value)} className="min-w-0 flex-1 bg-transparent text-[11px] text-ink outline-none placeholder:text-slate-400" placeholder="Search conversations" aria-label="Search conversations" /></label>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {filteredConversations.length === 0 && <p className="px-2 py-4 text-[10px] leading-4 text-muted">{conversations.isLoading ? "Loading conversations…" : historySearch ? "No matching conversations." : "Your recent conversations will appear here."}</p>}
            <div className="space-y-1.5">
              {filteredConversations.map((item) => <div key={item.id} className={"group rounded-xl border px-1.5 py-1.5 transition " + (item.id === activeConversationId ? "border-blue-200 bg-blue-50/80" : "border-transparent hover:border-slate-200 hover:bg-slate-50")}>
                {renamingConversationId === item.id ? <form onSubmit={(event) => saveRename(event, item.id)} className="flex items-center gap-1"><input autoFocus value={renameDraft} maxLength={1000} onChange={(event) => setRenameDraft(event.target.value)} className="min-w-0 flex-1 rounded border border-line bg-panel px-2 py-1.5 text-[10px] text-ink outline-none focus:border-blue-500" aria-label="Conversation name" /><button type="submit" className="rounded p-1 text-blue-600 hover:bg-blue-50" aria-label="Save conversation name"><Check size={13} /></button><button type="button" onClick={() => setRenamingConversationId(null)} className="rounded p-1 text-muted hover:bg-panel-raised" aria-label="Cancel rename"><X size={13} /></button></form> : <>
                  <button type="button" onClick={() => chooseConversation(item.id)} disabled={answer.isPending} title={item.title} className="flex w-full min-w-0 items-start gap-2 text-left disabled:opacity-50">
                    <span className="mt-0.5 shrink-0 text-muted">{item.is_pinned ? <Pin size={12} className="text-blue-500" /> : <MessageSquarePlus size={12} />}</span>
                    <span className="min-w-0 flex-1"><span className="block truncate text-[10px] font-medium text-ink">{item.title || item.document_filename}</span><span className="mt-0.5 block truncate text-[9px] text-muted">{item.document_filenames?.join(", ") || "General chat"}</span></span>
                  </button>
                  <div className="mt-1 flex items-center justify-end gap-0.5 opacity-70 transition group-hover:opacity-100">
                    <button type="button" onClick={() => updateChat.mutate({ id: item.id, change: { is_pinned: !item.is_pinned } })} className="rounded p-1 text-muted hover:bg-panel hover:text-blue-600" aria-label={item.is_pinned ? "Unpin conversation" : "Pin conversation"} title={item.is_pinned ? "Unpin" : "Pin"}>{item.is_pinned ? <PinOff size={11} /> : <Pin size={11} />}</button>
                    <button type="button" onClick={() => startRename(item)} className="rounded p-1 text-muted hover:bg-panel hover:text-ink" aria-label="Rename conversation" title="Rename"><Pencil size={11} /></button>
                    <button type="button" onClick={() => { if (window.confirm("Delete this conversation and its saved questions and answers?")) deleteChat.mutate(item.id); }} className="rounded p-1 text-muted hover:bg-red-50 hover:text-red-600" aria-label="Delete conversation" title="Delete"><Trash2 size={11} /></button>
                  </div>
                </>}
              </div>)}
            </div>
          </div>
          <button type="button" onClick={openSettings} className="flex shrink-0 items-center gap-2 border-t border-slate-200 bg-white/70 px-3 py-3 text-left text-[10px] font-medium text-slate-600 hover:bg-slate-100 hover:text-ink"><Settings2 size={14} />AI settings</button>
        </>}
      </aside>

    <section aria-label="AI assistant" className="relative flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-slate-50/40">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white/80 px-4 py-3.5 backdrop-blur-sm sm:px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          <Button variant="ghost" size="icon" className="rounded-xl md:hidden" onClick={() => setMobileHistoryOpen(true)} aria-label="Open conversation history" title="Conversation history"><PanelLeftOpen size={16} /></Button>
          <div className="grid size-9 place-items-center rounded-xl bg-blue-600 text-white shadow-sm shadow-blue-600/20"><Bot size={17} /></div>
          <div className="min-w-0">
          <h1 className="text-base font-semibold tracking-tight text-ink sm:text-lg">AI Assistant</h1>
          <p className="mt-0.5 truncate text-xs text-slate-500">Ask questions about your financial documents.</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button variant="ghost" size="icon" className="rounded-xl hover:bg-slate-100" onClick={openSettings} aria-label="Open AI settings" title="AI settings"><Settings2 size={17} /></Button>
          <Button variant="ghost" size="icon" className="rounded-xl hover:bg-slate-100" onClick={newConversation} disabled={answer.isPending} aria-label="Start a new conversation" title="New conversation"><MessageSquarePlus size={17} /></Button>
        </div>
      </header>

      <div ref={chatScroll} onScroll={(event) => { const element = event.currentTarget; shouldStickToBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96; }} className="min-h-0 min-w-0 flex-1 space-y-4 overflow-y-auto overscroll-contain bg-slate-50/50 p-3.5 sm:p-5" aria-live="polite">
        {messagesQuery.isError && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-700">Could not load this conversation. Refresh your documents and try again.</p>}
        {messagesQuery.isLoading && <p className="flex items-center gap-2 text-xs text-muted"><LoaderCircle size={14} className="animate-spin" />Loading saved conversation...</p>}
        {documents.isError && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-700">Could not load your document list. You can still ask a general question.</p>}
        {messages.length === 0 && <div className="mx-auto w-full max-w-2xl rounded-[24px] border border-slate-200 bg-white p-4 shadow-[0_12px_25px_rgba(15,23,42,0.04)] sm:p-5">
          <p className="text-center text-xl font-semibold tracking-tight text-ink sm:text-2xl">How can I help you today?</p>
          <p className="mt-2 text-center text-xs leading-5 text-slate-500">Ask questions, upload documents, analyze financial information and explore ideas.</p>
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {suggestions.map((prompt) => <button type="button" key={prompt} onClick={() => submit(prompt)} className="min-h-10 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-left text-xs leading-5 text-ink transition hover:border-blue-300 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50">{prompt}</button>)}
          </div>
        </div>}
        {messages.map((message) => <article key={message.id} className={"min-w-0 max-w-[95%] [overflow-wrap:anywhere] rounded-[22px] border px-4 py-3 text-xs leading-5 shadow-sm " + (message.role === "user" ? "ml-auto border-blue-600 bg-blue-600 text-white" : message.error ? "border-red-200 bg-red-50 text-red-800" : "border-slate-200 bg-white text-ink")}>
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
        {answer.isPending && <div className="flex w-fit items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600"><LoaderCircle size={14} className="animate-spin" />Generating response...</div>}
        {messages.length > 0 && !answer.isPending && <div className="flex flex-wrap gap-1.5">{["What should I verify?", "Explain this more simply", "Which page supports that?"].map((prompt) => <button key={prompt} type="button" onClick={() => submit(prompt)} className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[10px] text-slate-600 hover:border-blue-300 hover:text-ink disabled:opacity-50">{prompt}</button>)}</div>}
      </div>

      <form onSubmit={onSubmit} className="shrink-0 border-t border-slate-200 bg-white/85 p-3 sm:p-4">
        <input ref={uploadInput} type="file" multiple accept=".pdf,.docx,.txt,.csv,.xlsx,.json,.jsonl,.png,.jpg,.jpeg,.tif,.tiff" className="hidden" aria-label="Attach documents" onChange={(event) => { if (event.currentTarget.files) addFiles(event.currentTarget.files); event.currentTarget.value = ""; }} />
        {attachedFiles.length > 0 && <div className="mb-2 flex flex-wrap gap-1.5" aria-label="Attached files">
          {attachedFiles.map((file, index) => <span key={`${file.name}-${file.lastModified}-${index}`} className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-line bg-panel-raised px-2 py-1 text-[9px] text-ink"><FileText size={11} className="shrink-0 text-blue-500" /><span className="max-w-48 truncate">{file.name}</span><button type="button" onClick={() => setAttachedFiles((items) => items.filter((_, itemIndex) => itemIndex !== index))} className="rounded p-0.5 text-muted hover:bg-panel hover:text-ink" aria-label={`Remove ${file.name}`}><X size={11} /></button></span>)}
        </div>}
        <div className="flex items-end gap-2">
          <textarea value={draft} onChange={(event) => setDraft(event.target.value.slice(0, 500))} rows={2} maxLength={500} className="min-h-10 min-w-0 flex-1 resize-y rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs text-ink placeholder:text-slate-400 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20" placeholder="Ask a question about anything..." aria-label="Ask BankEase AI a question" onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(draft); } }} />
          {answer.isPending ? uploadStatus === "streaming" ? <Button type="button" variant="secondary" className="shrink-0" size="icon" onClick={stopGeneration} aria-label="Stop generation" title="Stop generation"><Square size={13} /></Button> : <Button type="button" variant="secondary" className="shrink-0" size="icon" disabled aria-label="Preparing documents"><LoaderCircle size={15} className="animate-spin" /></Button> : <Button type="submit" className="shrink-0" size="icon" aria-label="Send question" disabled={!draft.trim() && !attachedFiles.length}><Send size={15} /></Button>}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-[10px]" onClick={() => uploadInput.current?.click()} disabled={answer.isPending || effectiveDocumentIds.length + attachedFiles.length >= 5} aria-label="Attach files"><Paperclip size={12} />Attach</Button>
          {installedModelNames.length > 0 && <label className="flex items-center gap-1.5 text-[9px] text-muted"><span>Model</span><select aria-label="Select local AI model" value={requestedModel} onChange={(event) => {
            const model = event.currentTarget.value;
            setModelOverride(model);
            setSavedPreferenceOverride({ mode, model });
            try { localStorage.setItem(`${AI_SETTINGS_KEY}:${userId}`, JSON.stringify({ mode, model } satisfies AiPreferences)); }
            catch { toast.error("The model is selected for this session but could not be saved on this device."); }
          }} className="max-w-48 rounded-md border border-line bg-panel-raised px-2 py-1 text-[9px] text-ink outline-none focus:border-blue-500">{installedModelNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>}
          {uploadStatus && <span role="status" className="ml-auto inline-flex items-center gap-1.5 text-[9px] text-muted"><LoaderCircle size={11} className="animate-spin" />{uploadStatus === "streaming" ? "Generating response…" : uploadStatus}</span>}
          {!uploadStatus && <span className="ml-auto text-[9px] text-muted">{effectiveDocumentIds.length + attachedFiles.length}/5 documents</span>}
        </div>
        <p className="mt-1 text-[9px] text-muted">Enter to send · Shift+Enter for a new line · Answers may be incomplete; verify important details against sources.</p>
      </form>
    </section>
    {dragActive && <div className="pointer-events-none absolute inset-2 z-20 grid place-items-center rounded-xl border-2 border-dashed border-blue-500 bg-blue-500/10 text-sm font-semibold text-blue-700">Drop documents to attach</div>}
    </div>

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
