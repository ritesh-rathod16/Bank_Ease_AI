"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, CalendarClock, CheckCircle2, CircleAlert, FileText, LoaderCircle, MessageSquareText, Sparkles } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import { formatCurrency, formatDate, humanize } from "@/lib/utils";
import type { AskResult, DocumentSummary } from "@/types/api";

const summaryQuestion = "Summarize this document in plain English for a borrower. Include the purpose, parties involved, key amounts, dates, obligations, fees, risks, and items to review. Use only facts from the document and say 'not clearly stated in this document' when a detail is missing. Cite the page or source for each major point.";
const summarizeableTypes = new Set(["loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure", "insurance_policy", "financial_statement", "audit_report"]);

function requestId() {
  return crypto.randomUUID().replaceAll("-", "");
}

export function Document360View({ documents, selectedId, onSelect }: { documents: DocumentSummary[]; selectedId: string; onSelect: (id: string) => void }) {
  const queryClient = useQueryClient();
  const [summaryText, setSummaryText] = useState("");
  const [summarySources, setSummarySources] = useState<AskResult["sources"]>([]);
  const document = useQuery({ queryKey: ["document", selectedId], queryFn: () => api.document(selectedId), enabled: Boolean(selectedId) });
  const dashboard = useQuery({ queryKey: ["dashboard-summary"], queryFn: api.dashboard, enabled: Boolean(selectedId) });
  const reviews = useQuery({ queryKey: ["reviews", selectedId], queryFn: () => api.reviews(selectedId), enabled: Boolean(selectedId) });
  const summaries = useMutation({
    mutationFn: async () => {
      if (!document.data) throw new Error("Choose an available document first.");
      const conversation = await api.createConversation([document.data.id], `Simple summary: ${document.data.filename}`);
      const response = await api.sendConversationMessageStream(conversation.id, summaryQuestion, requestId(), {
        mode: "local", allow_hosted: false, allow_hosted_documents: false, web_research: false,
      }, (delta) => setSummaryText((current) => current + delta));
      return { response, conversationId: conversation.id };
    },
    onMutate: () => { setSummaryText(""); setSummarySources([]); },
    onSuccess: async ({ response }) => {
      setSummaryText(response.answer.answer);
      setSummarySources(response.answer.sources ?? []);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      toast.success("Simple document summary generated.");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Could not generate the document summary."),
  });

  const doc = document.data;
  const sourceFields = Object.entries(doc?.terms ?? {}).filter(([, term]) => term.effective_value !== null && term.effective_value !== undefined || term.value !== null && term.value !== undefined);
  const dates = sourceFields.filter(([name]) => name.endsWith("_date"));
  const obligations = dashboard.data?.upcoming_obligations.filter((item) => item.document_id === selectedId) ?? [];
  const activeReviews = reviews.data?.reviews.filter((task) => !["approved", "rejected"].includes(task.status)) ?? [];
  const clauses = doc?.clauses ?? [];
  const isReady = doc && ["completed", "needs_review"].includes(doc.status);
  const supportsAi = Boolean(doc && (summarizeableTypes.has(doc.doc_type) || doc.doc_type.startsWith("bank_statement_") || (doc.source_locator_type === "section" && doc.section_count)));

  return <div className="space-y-4">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div><Eyebrow>DOCUMENT 360° VIEW</Eyebrow><h2 className="mt-1 text-lg font-semibold">A consolidated view of your saved analysis</h2><p className="mt-1 text-xs text-muted">Extracted values stay linked to their source evidence. No risk score or unsupported finding is inferred here.</p></div>
      <label className="grid min-w-[min(100%,18rem)] gap-1 text-[10px] font-medium text-muted">Choose a document
        <select value={selectedId} onChange={(event) => { onSelect(event.target.value); setSummaryText(""); setSummarySources([]); }} className="min-h-10 rounded-lg border border-line bg-panel px-3 text-xs text-ink">
          {documents.map((item) => <option key={item.id} value={item.id}>{item.filename}</option>)}
        </select>
      </label>
    </div>

    {!selectedId && <Surface className="p-6 text-sm text-muted">Upload a document to build its consolidated view.</Surface>}
    {selectedId && document.isLoading && <Surface className="flex items-center gap-2 p-6 text-xs text-muted"><LoaderCircle className="animate-spin" size={15} />Loading saved document analysis…</Surface>}
    {selectedId && document.isError && <Surface className="p-5"><p role="alert" className="text-sm font-semibold">Document details could not be loaded.</p><p className="mt-2 text-xs text-muted">{document.error.message}</p><Button className="mt-3" size="sm" variant="secondary" onClick={() => void document.refetch()}>Try again</Button></Surface>}

    {doc && <>
      <Surface className="p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="grid size-9 shrink-0 place-items-center rounded-lg bg-blue-50 text-blue-700"><FileText size={17} /></span><Eyebrow>{humanize(doc.doc_type || "document")}</Eyebrow><span className="rounded-full border border-line bg-panel-raised px-2 py-1 text-[10px] text-muted">{humanize(doc.status)}</span></div><h3 className="mt-3 break-words text-lg font-semibold">{doc.filename}</h3><p className="mt-1 text-[11px] text-muted">Uploaded {formatDate(doc.created_at)} · Updated {formatDate(doc.updated_at)} · {doc.page_count ? `${doc.page_count} pages` : doc.transaction_count !== undefined ? `${doc.transaction_count} statement rows` : "Source size not recorded"}</p></div>
          <div className="flex flex-wrap gap-2"><Link href={`/dashboard/documents/${doc.id}`} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-line bg-panel-raised px-3 text-xs font-semibold text-ink hover:border-teal/40"><ArrowUpRight size={14} />Open full analysis</Link><Link href={`/dashboard/assistant?document_id=${encodeURIComponent(doc.id)}`} className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-blue-600 px-3 text-xs font-semibold text-white hover:bg-blue-700"><MessageSquareText size={14} />Ask AI</Link></div>
        </div>
        {doc.analysis_note && <div className="mt-4 rounded-lg border border-line bg-panel-raised p-3"><p className="text-[10px] font-semibold uppercase tracking-wide text-muted">Saved processing note</p><p className="mt-1 text-xs leading-5">{doc.analysis_note}</p></div>}
      </Surface>

      <div className="grid gap-4 xl:grid-cols-2">
        <Surface className="p-4 sm:p-5">
          <div className="flex items-start justify-between gap-3"><div><Eyebrow>AI SUMMARY</Eyebrow><h3 className="mt-1 text-sm font-semibold">Understand it in simple words</h3></div><Sparkles size={17} className="text-teal" /></div>
          {!summaryText && !summaries.isPending && <p className="mt-3 text-xs leading-5 text-muted">Generate a cited summary with your configured local AI model. The summary is saved as a conversation so you can continue with follow-up questions.</p>}
          {summaryText && <div className="mt-3 whitespace-pre-wrap break-words text-xs leading-6">{summaryText}</div>}
          {summaries.isPending && !summaryText && <p className="mt-3 flex items-center gap-2 text-xs text-muted"><LoaderCircle className="animate-spin" size={14} />Starting the local model…</p>}
          {summaries.isError && <p role="alert" className="mt-3 text-xs text-red-700">{summaries.error instanceof Error ? summaries.error.message : "Summary generation failed."}</p>}
          {summarySources.length > 0 && <div className="mt-4 space-y-2 border-t border-line pt-3"><p className="text-[10px] font-semibold uppercase tracking-wide text-muted">Cited sources</p>{summarySources.map((source, index) => <blockquote key={`${source.document_id ?? source.filename}-${source.page}-${index}`} className="border-l-2 border-teal/50 pl-3 text-[10px] leading-4 text-muted"><span className="font-semibold text-ink">{source.filename ?? doc.filename}{source.page ? ` · page ${source.page}` : source.section ? ` · section ${source.section}` : ""}</span><p className="mt-1">{source.text}</p></blockquote>)}</div>}
          <Button className="mt-4" size="sm" disabled={!isReady || !supportsAi || summaries.isPending} onClick={() => summaries.mutate()}>{summaries.isPending ? <LoaderCircle className="animate-spin" size={13} /> : <Sparkles size={13} />}{summaryText ? "Regenerate simple summary" : "Generate simple summary"}</Button>
          {!supportsAi && isReady && <p className="mt-2 text-[10px] text-muted">This document type is not currently supported as AI chat context.</p>}
          {!isReady && <p className="mt-2 text-[10px] text-muted">The summary becomes available after document processing finishes.</p>}
        </Surface>

        <Surface className="p-4 sm:p-5">
          <div className="flex items-start justify-between gap-3"><div><Eyebrow>IMPORTANT DATES</Eyebrow><h3 className="mt-1 text-sm font-semibold">Dates and confirmed obligations</h3></div><CalendarClock size={17} className="text-teal" /></div>
          {dates.length || obligations.length ? <div className="mt-3 space-y-3">
            {dates.map(([name, term]) => <div key={name} className="rounded-lg border border-line bg-panel-raised p-3"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-medium">{humanize(name)}</span><span className="text-xs font-semibold tabular-nums">{formatDate(String(term.effective_value ?? term.value))}</span></div><p className="mt-1 text-[10px] leading-4 text-muted">{term.evidence ?? "No supporting passage was extracted."}{term.page ? ` · Page ${term.page}` : ""}</p></div>)}
            {obligations.map((item) => <div key={`${item.document_id}-${item.due_date}-${item.title}`} className="rounded-lg border border-line bg-panel-raised p-3"><p className="text-xs font-medium">{item.title} · {formatDate(item.due_date)}</p><p className="mt-1 text-[10px] leading-4 text-muted">Owner-confirmed date · {item.date_evidence}{item.date_page ? ` · Page ${item.date_page}` : ""}</p></div>)}
          </div> : <p className="mt-3 text-xs leading-5 text-muted">No extracted date or owner-confirmed obligation is available for this document.</p>}
        </Surface>

        <Surface className="p-4 sm:p-5">
          <div className="flex items-start justify-between gap-3"><div><Eyebrow>EXTRACTED INFORMATION</Eyebrow><h3 className="mt-1 text-sm font-semibold">Key values and source evidence</h3></div><CheckCircle2 size={17} className="text-teal" /></div>
          {sourceFields.length ? <div className="mt-3 divide-y divide-line">{sourceFields.slice(0, 12).map(([name, term]) => <div key={name} className="grid gap-1 py-3 sm:grid-cols-[minmax(9rem,.65fr)_minmax(0,1.35fr)] sm:gap-4"><div><p className="text-[11px] font-medium">{humanize(name)}</p><p className="mt-1 text-xs tabular-nums">{typeof (term.effective_value ?? term.value) === "number" && ["loan_amount", "emi", "processing_fee", "late_fee", "sum_insured", "premium_amount"].includes(name) ? formatCurrency(term.effective_value ?? term.value) : String(term.effective_value ?? term.value)}{name === "interest_rate" ? "%" : ""}</p></div><p className="text-[10px] leading-4 text-muted">{term.evidence ?? "No source passage found."}{term.page ? ` · Page ${term.page}` : ""}</p></div>)}</div> : <p className="mt-3 text-xs leading-5 text-muted">No extracted terms are available for this document.</p>}
            {doc.statement_analysis && <div className="mt-4 grid gap-2 border-t border-line pt-4 sm:grid-cols-3">{[["Transactions", doc.statement_analysis.transaction_count], ["Opening balance", doc.statement_analysis.opening_balance_minor === null ? "Not found" : formatCurrency(doc.statement_analysis.opening_balance_minor / 100)], ["Closing balance", doc.statement_analysis.closing_balance_minor === null ? "Not found" : formatCurrency(doc.statement_analysis.closing_balance_minor / 100)]].map(([label, value]) => <div key={String(label)} className="rounded-lg border border-line bg-panel-raised p-3"><p className="text-[10px] text-muted">{label}</p><p className="mt-1 text-xs font-semibold tabular-nums">{value}</p></div>)}</div>}
        </Surface>

        <Surface className="p-4 sm:p-5">
          <div className="flex items-start justify-between gap-3"><div><Eyebrow>REVIEW & RISK INDICATORS</Eyebrow><h3 className="mt-1 text-sm font-semibold">Items that may need attention</h3></div><CircleAlert size={17} className="text-amber" /></div>
          {reviews.isLoading && <p className="mt-3 text-xs text-muted">Loading review findings…</p>}
          {reviews.isError && <p role="alert" className="mt-3 text-xs text-red-700">Could not load review findings: {reviews.error.message}</p>}
          {activeReviews.length > 0 && <div className="mt-3 space-y-2">{activeReviews.map((task) => <article key={task.id} className="rounded-lg border border-amber/20 bg-amber/5 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs font-medium">{humanize(task.field_id)}</p><span className="rounded-full bg-amber/10 px-2 py-1 text-[10px] text-amber">{humanize(task.status)}</span></div><p className="mt-1 text-[10px] leading-4 text-muted">{task.reason}</p>{task.snapshot?.evidence && <blockquote className="mt-2 border-l-2 border-amber/40 pl-2 text-[10px] leading-4 text-muted">{task.snapshot.evidence}{task.snapshot.page ? ` · Page ${task.snapshot.page}` : ""}</blockquote>}</article>)}</div>}
          {clauses.length > 0 && <div className="mt-3 space-y-2">{clauses.slice(0, 6).map((clause, index) => <article key={`${clause.category}-${clause.page}-${index}`} className="rounded-lg border border-line bg-panel-raised p-3"><p className="text-xs font-medium">{clause.category} · Page {clause.page}</p><p className="mt-1 line-clamp-3 text-[10px] leading-4 text-muted">{clause.text}</p><p className="mt-2 text-[10px] leading-4 text-amber">Review note: {clause.attention_point}</p></article>)}</div>}
          {!reviews.isLoading && activeReviews.length === 0 && clauses.length === 0 && <p className="mt-3 text-xs leading-5 text-muted">No open review task or rule-matched clause is saved for this document. This does not mean that the document is risk-free.</p>}
        </Surface>
      </div>
    </>}
  </div>;
}
