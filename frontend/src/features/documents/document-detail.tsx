"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, ClipboardCheck, ExternalLink, LoaderCircle, MessageSquareText, Pencil, RotateCcw, Trash2, X } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import { formatCurrency, formatDate, humanize } from "@/lib/utils";
import { ApiError } from "@/lib/api/client";
import { StatementWorkspace } from "@/features/documents/statement-workspace";
import { SourcePreview } from "@/features/documents/source-preview";
import { VersionHistory } from "@/features/documents/version-history";
import type { Term } from "@/types/api";

const correctionSchema = z.object({ value: z.string().trim().min(1, "Enter the corrected value.").max(120), reason: z.string().max(500) });
const reviewReasonSchema = z.object({ reason: z.string().max(500) });
type CorrectionForm = z.infer<typeof correctionSchema>;
const loanDocumentTypes = new Set(["loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure"]);

function DocumentAssistantCard({ id, contextAvailable = true }: { id: string; contextAvailable?: boolean }) {
  return <Surface className="border-blue-200/70 p-5 sm:p-6">
    <div className="flex items-center gap-2"><MessageSquareText className="text-teal" size={17} /><Eyebrow>AI ASSISTANT</Eyebrow></div>
    <p className="mt-2 text-xs leading-5 text-muted">{contextAvailable
      ? "Ask BankEase AI about this document. It can explain the evidence it finds and answer broader financial questions in the same conversation."
      : "Open BankEase AI for general financial questions. This document type is not currently available as chat context."}</p>
    <Link href={`/dashboard/assistant?document_id=${encodeURIComponent(id)}`} className="mt-4 inline-flex min-h-9 items-center gap-2 rounded-lg bg-blue-600 px-3.5 text-xs font-semibold text-white transition hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2">
      <MessageSquareText size={14} />Ask AI about this document
    </Link>
  </Surface>;
}

function evidenceLabel(term: Term) {
  if (!term.evidence_status) return null;
  if (term.verification_status === "verified") return "Source text and extracted value matched";
  if (term.evidence_status === "matched") return "Text located; review against the original";
  if (term.evidence_status === "not_found") return "No source passage located";
  if (term.evidence_status === "invalid_page") return "Source page reference is invalid";
  if (term.evidence_status === "invalid_bbox") return "Source highlight bounds need review";
  return "Source evidence needs review";
}

export function DocumentDetail({ id }: { id: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [finance, setFinance] = useState<Awaited<ReturnType<typeof api.calculate>> | null>(null);
  const [editingField, setEditingField] = useState<string | null>(null);
  const [requestingField, setRequestingField] = useState<string | null>(null);
  const document = useQuery({ queryKey: ["document", id], queryFn: () => api.document(id) });
  const backgroundStatus = useQuery({
    queryKey: ["document-status", id], queryFn: () => api.documentStatus(id),
    enabled: document.data?.status === "processing",
    refetchInterval: (query) => ["processing", "queued"].includes(query.state.data?.job?.status ?? "") ? 1500 : false,
  });
  useEffect(() => {
    if (backgroundStatus.data && !["processing", "queued"].includes(backgroundStatus.data.job?.status ?? "")) {
      void queryClient.invalidateQueries({ queryKey: ["document", id] });
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    }
  }, [backgroundStatus.data, id, queryClient]);
  const reviewTasks = useQuery({ queryKey: ["reviews", id], queryFn: () => api.reviews(id), enabled: Boolean(document.data) });
  const clauses = useQuery({
    queryKey: ["clauses", id], queryFn: () => api.clauses(id),
    enabled: Boolean(document.data && !document.data.doc_type.startsWith("bank_statement_") && !["kyc_form", "financial_statement"].includes(document.data.doc_type)),
  });
  const correctionForm = useForm<CorrectionForm>({ resolver: zodResolver(correctionSchema), defaultValues: { value: "", reason: "" } });
  const reviewForm = useForm<z.infer<typeof reviewReasonSchema>>({ resolver: zodResolver(reviewReasonSchema), defaultValues: { reason: "Please verify this extracted value against its cited source." } });
  const calculate = useMutation({ mutationFn: () => api.calculate(id), onSuccess: setFinance, onError: (error) => toast.error(error.message) });
  const correction = useMutation({
    mutationFn: ({ field, values }: { field: string; values: CorrectionForm }) => api.correctField(id, field, values.value, values.reason),
    onSuccess: async () => { setEditingField(null); setFinance(null); await Promise.all([queryClient.invalidateQueries({ queryKey: ["document", id] }), queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] })]); toast.success("Correction saved with its source evidence and history."); },
    onError: (error) => toast.error(error.message),
  });
  const revertCorrection = useMutation({
    mutationFn: (field: string) => api.correctField(id, field, null),
    onSuccess: async () => { setEditingField(null); setFinance(null); await queryClient.invalidateQueries({ queryKey: ["document", id] }); toast.success("Correction reverted; the extracted value and audit history remain."); },
    onError: (error) => toast.error(error.message),
  });
  const requestReview = useMutation({
    mutationFn: ({ field, reason }: { field: string; reason: string }) => api.requestReview(id, field, reason),
    onSuccess: async (result) => { setRequestingField(null); await queryClient.invalidateQueries({ queryKey: ["reviews", id] }); toast.success(result.created ? "Review requested." : "A review for this field is already open."); },
    onError: (error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: () => api.removeDocument(id),
    onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["documents"] }), queryClient.invalidateQueries({ queryKey: ["conversations"] }), queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] })]); queryClient.removeQueries({ queryKey: ["conversation-messages"] }); toast.success("Document analysis deleted."); router.push("/dashboard/documents"); },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Could not delete this document."),
  });
  const retryProcessing = useMutation({
    mutationFn: () => api.retryDocument(id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["document", id] }),
        queryClient.invalidateQueries({ queryKey: ["document-status", id] }),
        queryClient.invalidateQueries({ queryKey: ["documents"] }),
      ]);
      toast.success("Analysis queued again.");
    },
    onError: (error) => toast.error(error.message),
  });

  if (document.isLoading) return <div className="flex items-center gap-2 py-16 text-xs text-muted"><LoaderCircle className="animate-spin" size={15} />Loading document evidence…</div>;
  if (document.isError || !document.data) return <Surface className="p-6"><p className="text-sm font-semibold">Document unavailable</p><p className="mt-2 text-xs text-muted">{document.error instanceof ApiError && document.error.status === 404 ? "It may have been deleted or belong to another account." : document.error?.message}</p><Link className="mt-4 inline-block text-xs text-teal hover:underline" href="/dashboard/documents">Return to my documents</Link></Surface>;

  const doc = document.data;
  const isInsurance = doc.doc_type === "insurance_policy";
  const isKyc = doc.doc_type === "kyc_form";
  const isFinancial = doc.doc_type === "financial_statement";
  const isAudit = doc.doc_type === "audit_report";
  const isLoan = loanDocumentTypes.has(doc.doc_type);
  if (doc.status === "processing" || doc.status === "failed") return <div className="space-y-5">
    <Link href="/dashboard/documents" className="inline-flex items-center gap-2 text-xs text-muted hover:text-teal"><ArrowLeft size={14} />My documents</Link>
    <Surface className="mx-auto max-w-2xl p-6 shadow-sm sm:p-8">
      <div className={`grid size-11 place-items-center rounded-xl ${doc.status === "failed" ? "bg-red-50 text-red-700" : "bg-blue-50 text-blue-700"}`}>
        {doc.status === "failed" ? <X size={20} /> : <LoaderCircle className="animate-spin" size={20} />}
      </div>
      <h1 className="mt-4 text-lg font-semibold">{doc.status === "failed" ? "Analysis needs another try" : "Your document is being analyzed"}</h1>
      <p className="mt-1 break-all text-xs font-medium text-slate-700">{doc.filename}</p>
      {doc.status === "processing" ? <p className="mt-3 text-xs leading-5 text-muted">This upload is safely stored in your private workspace. Text extraction and document checks run in a background worker; this page will update when they finish.</p>
        : <p role="alert" className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-800">{doc.error || "The document could not be analyzed."}</p>}
      {doc.status === "processing" && backgroundStatus.data?.job && <p className="mt-3 text-[10px] text-muted">Job: {humanize(backgroundStatus.data.job.status)} · Attempt {backgroundStatus.data.job.attempts} of {backgroundStatus.data.job.max_attempts}</p>}
      <div className="mt-5 flex flex-wrap gap-2">
        {doc.status === "failed" && <Button disabled={retryProcessing.isPending} onClick={() => retryProcessing.mutate()}>{retryProcessing.isPending ? <LoaderCircle className="animate-spin" size={14} /> : <RotateCcw size={14} />}Retry analysis</Button>}
        <Button variant="secondary" onClick={() => void document.refetch()}>Refresh status</Button>
        <Button variant="ghost" disabled={remove.isPending} onClick={() => { if (window.confirm(`Delete ${doc.filename} and its queued analysis?`)) remove.mutate(); }}><Trash2 size={14} />Delete upload</Button>
      </div>
      <SourcePreview id={id} filename={doc.filename} available={doc.source_available} />
    </Surface>
  </div>;
  if (doc.doc_type.startsWith("bank_statement_")) return <div className="space-y-6">
    <StatementWorkspace
      id={id}
      document={doc}
      deleting={remove.isPending}
      onDelete={() => { if (window.confirm(`Delete ${doc.filename}? This removes the imported statement analysis.`)) remove.mutate(); }}
    />
    <VersionHistory id={id} docType={doc.doc_type} />
  </div>;
  if (doc.doc_type === "unknown") return <div className="space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-4"><div><Link href="/dashboard/documents" className="inline-flex items-center gap-2 text-xs text-muted hover:text-teal"><ArrowLeft size={14} />My documents</Link><Eyebrow className="mt-5">TEXT EXTRACTED · CLASSIFICATION NEEDED</Eyebrow><h1 className="mt-2 break-words text-2xl font-semibold tracking-tight sm:text-3xl">{doc.filename}</h1><p className="mt-2 text-xs text-muted">{doc.source_locator_type === "section" ? `${doc.section_count ?? 0} text sections · page layout unavailable` : `${doc.page_count} pages`} · Structured analysis unavailable</p></div><div className="flex flex-wrap gap-2"><Link href={`/dashboard/assistant?document_id=${id}`} className="inline-flex min-h-8 items-center gap-2 rounded-lg bg-blue-600 px-3 text-xs font-semibold text-white hover:bg-blue-700"><MessageSquareText size={14} />AI Assistant</Link><Button variant="danger" size="sm" disabled={remove.isPending} onClick={() => { if (window.confirm("Delete this document analysis?")) remove.mutate(); }}><Trash2 size={14} />Delete</Button></div></div>
    <VersionHistory id={id} docType={doc.doc_type} />
    <Surface className="border-amber-200 bg-amber-50 p-4 sm:p-5"><p className="text-xs font-semibold text-amber-950">{doc.suggested_doc_type ? `Possible document type: ${humanize(doc.suggested_doc_type)}${doc.suggested_loan_category ? ` · ${humanize(doc.suggested_loan_category)} loan` : ""}` : "This file was not classified as a supported loan document or bank statement."}</p><p className="mt-2 text-xs leading-5 text-amber-900">{doc.analysis_note ?? "Readable text is available, but BankEase did not run structured loan or statement analysis. Check the original pages before relying on any information."}</p></Surface>
    <SourcePreview id={id} filename={doc.filename} available={doc.source_available} />
    <DocumentAssistantCard id={id} contextAvailable={doc.source_format === "docx" && doc.source_locator_type === "section"} />
  </div>;
  return <div className="space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-4"><div><Link href="/dashboard/documents" className="inline-flex items-center gap-2 text-xs text-muted hover:text-teal"><ArrowLeft size={14} />My documents</Link><Eyebrow className="mt-5">DOCUMENT REVIEW · {humanize(doc.doc_type || "Document")}</Eyebrow><h1 className="mt-2 break-words text-2xl font-semibold tracking-tight sm:text-3xl">{doc.filename}</h1><p className="mt-2 text-xs text-muted">{doc.page_count} pages · {humanize(doc.status)}{doc.ocr_pages?.length ? ` · OCR on pages ${doc.ocr_pages.join(", ")} — verify all extracted values against the scan` : ""}{doc.unreadable_pages?.length ? ` · Text unavailable on pages ${doc.unreadable_pages.join(", ")}` : ""}</p></div><div className="flex flex-wrap gap-2"><Link href={`/dashboard/assistant?document_id=${id}`} className="inline-flex min-h-8 items-center gap-2 rounded-lg bg-blue-600 px-3 text-xs font-semibold text-white hover:bg-blue-700"><MessageSquareText size={14} />AI Assistant</Link><a className="inline-flex min-h-8 items-center gap-2 rounded-lg border border-line bg-panel-raised px-3 text-xs font-semibold text-ink hover:border-teal/30 hover:text-teal" href={api.reportUrl(id)} target="_blank" rel="noreferrer"><ExternalLink size={14} />Open report</a><Button variant="danger" size="sm" disabled={remove.isPending} onClick={() => { if (window.confirm("Delete this document analysis?")) remove.mutate(); }}><Trash2 size={14} />Delete</Button></div></div>

    <VersionHistory id={id} docType={doc.doc_type} />

    <SourcePreview id={id} filename={doc.filename} available={doc.source_available} />

    {(isKyc || isFinancial || isAudit) && <Surface className="border-amber-200 bg-amber-50 p-4 sm:p-5"><p className="text-xs font-semibold text-amber-950">{isKyc ? "Review the original form before relying on these fields." : isFinancial ? "Check the original statement and its reporting units." : "Read audit passages in the context of the full report."}</p><p className="mt-2 text-xs leading-5 text-amber-900">{doc.analysis_note}</p></Surface>}

    <Surface className="overflow-hidden"><div className="border-b border-line px-5 py-4"><Eyebrow>{isKyc ? "EXTRACTED KYC FIELDS" : isFinancial ? "EXTRACTED STATEMENT TOTALS" : isAudit ? "LABELED AUDIT REPORT DETAILS" : isInsurance ? "EXTRACTED POLICY DETAILS" : "EXTRACTED TERMS"}</Eyebrow><p className="mt-1 text-xs text-muted">{isKyc ? "Only explicitly labeled name, birth date, and address fields are shown. Original source text and page evidence are retained; government identity numbers are not extracted." : isFinancial ? "Only labeled totals are extracted. Amounts keep their displayed units; unclear, conflicting, and comparative-column values stay for review." : isAudit ? "Labeled metadata remains tied to its source page. Quoted passages are not independent audit or legal interpretations." : isInsurance ? "Rule-detected details preserve their original source text and page. Verify all coverage and payment terms against the policy." : "Saved corrections affect calculations and comparisons. Original text, page evidence, and the correction history remain available."}</p></div>
      <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-left text-xs"><thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-muted"><tr><th className="px-5 py-3">Term</th><th className="px-4 py-3">Current value</th><th className="px-4 py-3">Source evidence</th><th className="px-4 py-3">Page</th><th className="px-4 py-3">Review and history</th></tr></thead><tbody className="divide-y divide-line">{Object.entries(doc.terms ?? {}).map(([key, term]) => {
        const value = term.effective_value ?? term.value;
        const corrected = Boolean(term.user_correction);
        const needsReview = ["review", "ambiguous", "conflict"].includes(term.status);
        const dateField = key.endsWith("_date");
        const calendarDateField = ["first_emi_date", "loan_maturity_date", "interest_reset_date", "policy_expiry_date"].includes(key);
        const openTask = reviewTasks.data?.reviews.find((task) => task.field_id === key && !["approved", "rejected"].includes(task.status));
        return <Fragment key={key}>
          <tr className="align-top"><td className="whitespace-nowrap px-5 py-4 font-medium">{humanize(key)}</td><td className="px-4 py-4 tabular-nums">{value === null ? <span className="text-muted">Not found</span> : <span className={corrected ? "text-teal" : ""}>{["loan_amount", "emi", "processing_fee", "late_fee", "sum_insured", "premium_amount"].includes(key) ? formatCurrency(value) : String(value)}{key === "interest_rate" ? "%" : ""}</span>}{term.raw && <p className="mt-1 text-[10px] leading-4 text-muted">Extracted source value: {term.raw}</p>}{corrected && <p className="mt-1 text-[10px] text-teal">Saved correction: {term.user_correction?.raw}</p>}<div className="mt-2 flex flex-wrap gap-1"><Button size="sm" variant="ghost" onClick={() => { setEditingField(key); correctionForm.reset({ value: String(value ?? term.raw ?? ""), reason: term.user_correction?.reason ?? "" }); }}><Pencil size={12} />Correct</Button>{corrected && <Button size="sm" variant="ghost" disabled={revertCorrection.isPending} onClick={() => revertCorrection.mutate(key)}><RotateCcw size={12} />Revert</Button>}</div>{openTask ? <p className="mt-2 text-[10px] text-amber">Review · {humanize(openTask.status)}</p> : <Button className="mt-2" size="sm" variant="ghost" onClick={() => { setRequestingField(requestingField === key ? null : key); reviewForm.reset({ reason: "Please verify this extracted value against its cited source." }); }}><ClipboardCheck size={12} />Request review</Button>}{requestingField === key && !openTask && <form className="mt-2 grid gap-2" onSubmit={reviewForm.handleSubmit(({ reason }) => requestReview.mutate({ field: key, reason }))}><label className="grid gap-1 text-[10px] text-muted">Reason (optional)<Input maxLength={500} {...reviewForm.register("reason")} /></label><div className="flex gap-1"><Button size="sm" disabled={requestReview.isPending}>{requestReview.isPending && <LoaderCircle className="animate-spin" size={12} />}Send</Button><Button type="button" size="sm" variant="ghost" onClick={() => setRequestingField(null)}>Cancel</Button></div></form>}</td><td className="max-w-[480px] whitespace-normal px-4 py-4 leading-5 text-muted">{term.evidence ?? "No source passage found."}{term.note && <p className="mt-1 text-amber">{term.note}</p>}</td><td className="px-4 py-4 text-muted">{term.page ? `p. ${term.page}` : "—"}</td><td className="px-4 py-4"><span className={`rounded-full px-2 py-1 text-[10px] ${needsReview ? "bg-amber/10 text-amber" : corrected ? "bg-teal/10 text-teal" : "bg-panel-raised text-muted"}`}>{corrected ? "Corrected" : humanize(term.status)}</span>{evidenceLabel(term) && <p className="mt-2 max-w-[180px] text-[10px] leading-4 text-muted">{evidenceLabel(term)}</p>}{term.correction_history?.length ? <details className="mt-2 max-w-[260px]"><summary className="cursor-pointer text-[10px] text-muted">History ({term.correction_history.length})</summary><ol className="mt-2 space-y-2">{term.correction_history.map((event) => <li key={event.id} className="border-l border-line pl-2 text-[10px] leading-4 text-muted"><span className="font-medium text-ink">{event.action === "set" ? "Correction saved" : "Correction reverted"}</span> · {formatDate(event.created_at)}<p>{String(event.before_value ?? "Not set")} → {String(event.after_value ?? "Extracted value")}</p>{event.reason && <p>{event.reason}</p>}</li>)}</ol></details> : null}</td></tr>
          {editingField === key && <tr><td colSpan={5} className="bg-white px-5 py-4"><form className="grid gap-3 md:grid-cols-[1fr_1fr_auto_auto] md:items-end" onSubmit={correctionForm.handleSubmit((values) => correction.mutate({ field: key, values }))}><label className="grid gap-1.5 text-[10px] font-medium text-muted">{dateField ? "Confirm date from source" : "Corrected value"}<Input type={dateField ? "date" : "text"} aria-label={`${dateField ? "Confirm" : "Corrected"} ${humanize(key)}`} maxLength={120} {...correctionForm.register("value")} />{dateField && <span>{calendarDateField ? "This confirmed date can appear in your financial calendar." : "Check this date against the cited policy page before saving."}</span>}{correctionForm.formState.errors.value && <span className="text-red-700">{correctionForm.formState.errors.value.message}</span>}</label><label className="grid gap-1.5 text-[10px] font-medium text-muted">Reason (optional)<Input aria-label="Correction reason" maxLength={500} {...correctionForm.register("reason")} /></label><Button type="submit" size="sm" disabled={correction.isPending}>{correction.isPending ? <LoaderCircle className="animate-spin" size={13} /> : <Check size={13} />}Save correction</Button><Button type="button" size="sm" variant="ghost" onClick={() => setEditingField(null)}><X size={13} />Cancel</Button></form></td></tr>}
        </Fragment>;
      })}</tbody></table></div>
    </Surface>

    {isFinancial && <Surface className="p-5 sm:p-6"><Eyebrow>ARITHMETIC CHECKS</Eyebrow><p className="mt-1 text-xs leading-5 text-muted">A same-page balance-sheet equation check compares the extracted totals as printed. It does not interpret accounting policy or prove the statement is accurate.</p><div className="mt-4 space-y-3">{(doc.financial_checks ?? []).map((check) => <article key={check.id} className="rounded-lg border border-line bg-slate-50 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-xs font-semibold">{check.label}</h2><span className={`rounded-full px-2 py-1 text-[10px] ${check.status === "consistent" ? "bg-emerald-50 text-emerald-800" : check.status === "difference" ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-900"}`}>{humanize(check.status)}</span></div><p className="mt-2 text-xs leading-5 text-muted">{check.summary}{check.difference !== undefined ? ` Difference: ${check.difference}` : ""}</p><div className="mt-3 grid gap-2 md:grid-cols-2">{check.sources.map((source, index) => <blockquote key={`${source.field}-${index}`} className="border-l-2 border-blue-300 pl-3 text-[10px] leading-4 text-slate-600"><span className="font-semibold text-slate-800">{humanize(source.field)} · {source.value}{source.page ? ` · page ${source.page}` : ""}</span><span className="mt-1 block">{source.evidence}</span></blockquote>)}</div></article>)}</div></Surface>}

    {!isKyc && <div className={`grid gap-5 ${isLoan ? "xl:grid-cols-2" : "xl:grid-cols-1"}`}>
      <DocumentAssistantCard id={id} />
      {isLoan && <Surface className="p-5 sm:p-6"><div className="flex items-center justify-between gap-3"><div><Eyebrow>REPAYMENT CHECK</Eyebrow><h2 className="mt-1 text-sm font-semibold">Reducing-balance estimate</h2></div><Button variant="secondary" size="sm" onClick={() => calculate.mutate()} disabled={calculate.isPending}>{calculate.isPending && <LoaderCircle className="animate-spin" size={14} />}Calculate EMI</Button></div><p className="mt-2 text-xs leading-5 text-muted">Uses the current field values, including saved corrections where present. This estimate does not include every lender fee or rate reset.</p>
        {finance && <div className="mt-5 grid grid-cols-2 gap-3">{[["Calculated EMI", formatCurrency(finance.calculated_emi)], ["Stated EMI", formatCurrency(finance.stated_emi)], ["Total repayment", formatCurrency(finance.total_repayment)], ["Estimated interest", formatCurrency(finance.total_interest)]].map(([label, value]) => <div key={label} className="rounded-lg border border-line bg-slate-50 p-3"><p className="text-[10px] text-muted">{label}</p><p className="mt-2 text-sm font-semibold tabular-nums">{value}</p></div>)}{finance.note && <p className="col-span-2 rounded-lg border border-amber/20 bg-amber/5 p-3 text-xs leading-5 text-amber">{finance.note}</p>}<ul className="col-span-2 space-y-1 text-[10px] leading-4 text-muted">{finance.assumptions.map((assumption) => <li key={assumption}>• {assumption}</li>)}</ul></div>}
      </Surface>}
    </div>}

    {!isKyc && !isFinancial && <Surface className="p-5 sm:p-6"><Eyebrow>{isAudit ? "AUDIT REPORT PASSAGES" : isInsurance ? "POLICY CONDITIONS TO REVIEW" : "CLAUSES TO REVIEW"}</Eyebrow><p className="mt-1 text-xs text-muted">Source passages are shown with their page and a neutral note for context.</p>{clauses.isLoading && <p className="mt-4 text-xs text-muted">Loading passages…</p>}{clauses.isError && <p role="alert" className="mt-4 text-xs text-red-700">{clauses.error.message}</p>}{clauses.data?.length === 0 && <p className="mt-4 text-xs text-muted">No matching passages were detected.</p>}<div className="mt-4 grid gap-3 md:grid-cols-2">{clauses.data?.map((clause, index) => <article key={`${clause.page}-${index}`} className="rounded-lg border border-line bg-slate-50 p-4"><div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold">{clause.category}</span><span className="text-[10px] text-teal">Page {clause.page}</span></div><p className="mt-3 text-xs leading-5 text-muted">{clause.text}</p><p className="mt-3 border-t border-line pt-3 text-[11px] leading-5 text-amber">Review note: {clause.attention_point}</p></article>)}</div></Surface>}
  </div>;
}
