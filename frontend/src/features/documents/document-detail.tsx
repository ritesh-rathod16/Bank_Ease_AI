"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, ClipboardCheck, ExternalLink, LoaderCircle, MessageSquareText, Pencil, RotateCcw, Trash2, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
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
import { AssistantPanel } from "@/features/dashboard/assistant-panel";
import type { AskResult, Term } from "@/types/api";

const correctionSchema = z.object({ value: z.string().trim().min(1, "Enter the corrected value.").max(120), reason: z.string().max(500) });
const reviewReasonSchema = z.object({ reason: z.string().max(500) });
type CorrectionForm = z.infer<typeof correctionSchema>;
const loanDocumentTypes = new Set(["loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure"]);
const summaryQuestion = "Summarize this document in plain English for a borrower. Include the purpose, parties involved, key amounts, dates, obligations, fees, risks, and items to review. Use only facts from the document and say 'not clearly stated in this document' when a detail is missing. Cite the page or source for each major point.";

function requestId() {
  return crypto.randomUUID().replaceAll("-", "");
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

function computeSummaryQuality(summaryText: string, summarySources: AskResult["sources"]) {
  const lowerText = summaryText.toLowerCase();
  const hasPlainLanguage = lowerText.includes("plain english") || lowerText.includes("in simple terms") || lowerText.includes("in simple language") || lowerText.length > 220;
  const hasUncertainty = lowerText.includes("not clearly stated") || lowerText.includes("not clearly stated in this document") || lowerText.includes("not stated");
  const hasMajorEvidence = summarySources.length > 0;
  const score = Math.min(100, Math.max(28, Math.round((hasMajorEvidence ? 35 : 10) + (hasPlainLanguage ? 25 : 8) + (hasUncertainty ? 20 : 8) + (summaryText.trim().length > 180 ? 20 : 6))));
  const label = score >= 85 ? "Strong" : score >= 70 ? "Good" : score >= 50 ? "Needs review" : "Weak";
  const note = score >= 85 ? "This summary is well grounded and easy to understand." : score >= 70 ? "This summary is useful, but a quick check is still recommended." : score >= 50 ? "This summary may be helpful, but the wording or evidence should be reviewed." : "This summary needs a tighter review before relying on it.";

  const strengths = [
    hasMajorEvidence ? "The summary references source-backed evidence." : "Evidence references are limited, so the summary should be checked against the original document.",
    hasPlainLanguage ? "The explanation is written in simpler, borrower-friendly language." : "The language is a bit technical and could be simplified further.",
    hasUncertainty ? "The summary flags missing or unclear information clearly." : "The summary could make missing document details more explicit.",
  ].filter(Boolean);

  const risks = [
    !hasMajorEvidence && "Source evidence is not clearly listed for this summary.",
    !hasPlainLanguage && "The explanation may be harder for a borrower to follow.",
    !hasUncertainty && "The summary may not clearly call out what the document does not say.",
  ].filter((item): item is string => Boolean(item));

  const actions = [
    "Compare the summary against the original source before finalising any decision.",
    "Check all dates, amounts, and obligations listed in the document again.",
    hasUncertainty ? "Keep the uncertainty notes in place so missing details remain visible." : "Add a clearer note about anything the document does not spell out.",
  ];

  return {
    score,
    label,
    note,
    strengths,
    risks,
    actions,
    checks: [
      { label: "Source-backed", passed: hasMajorEvidence },
      { label: "Plain-language explanation", passed: hasPlainLanguage },
      { label: "Uncertainty flagged", passed: hasUncertainty },
    ],
  };
}

function escapePdfText(value: string) {
  return value.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)").replace(/\r/g, " ").replace(/\n/g, " ").replace(/\t/g, " ");
}

function buildReviewPdfBlob(docName: string, summaryText: string, sources: AskResult["sources"]) {
  const allLines = [
    "BankEase AI - Document review",
    docName,
    "",
    "Plain-English summary",
    ...((summaryText || "Summary not available yet.").split(/\n+/).flatMap((paragraph) => {
      const words = paragraph.trim().split(/\s+/).filter(Boolean);
      const wrapped: string[] = [];
      let current = "";
      for (const word of words) {
        const candidate = current ? `${current} ${word}` : word;
        if (candidate.length > 92) {
          if (current) wrapped.push(current);
          current = word;
        } else {
          current = candidate;
        }
      }
      if (current) wrapped.push(current);
      return wrapped.length ? wrapped : [paragraph];
    })),
    "",
    "Evidence references:",
    ...(sources.length ? [] : ["No supporting passages were explicitly attached to this summary."]),
    ...sources.flatMap((source, index) => {
      const label = source.title ?? source.filename ?? `Evidence ${index + 1}`;
      const pageText = source.page ? `Page ${source.page}` : "Source excerpt";
      const text = source.text ? source.text : "No excerpt available.";
      return [`${label} · ${pageText}`, text.substring(0, 220)];
    }),
  ];

  const pages: string[][] = [[]];
  for (const line of allLines) {
    if (pages[pages.length - 1].length >= 38) {
      pages.push([]);
    }
    pages[pages.length - 1].push(line || "");
  }

  const objects: Record<number, string> = {};
  const kids: string[] = [];
  objects[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  objects[2] = "<< /Type /Pages /Kids [] /Count 0 >>";
  objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";

  let nextObjectId = 4;
  for (const pageLines of pages) {
    let stream = "BT\n/F1 10 Tf\n50 780 Td\n";
    for (let lineIndex = 0; lineIndex < pageLines.length; lineIndex += 1) {
      const line = pageLines[lineIndex];
      if (lineIndex > 0 && lineIndex % 10 === 0) {
        stream += "0 -120 Td\n";
      }
      stream += `(${escapePdfText(line)}) Tj\n`;
      if (lineIndex < pageLines.length - 1) {
        stream += "0 -14 Td\n";
      }
    }
    stream += "ET\n";

    const contentObjectId = nextObjectId++;
    objects[contentObjectId] = `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`;
    const pageObjectId = nextObjectId++;
    objects[pageObjectId] = `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents ${contentObjectId} 0 R /Resources << /Font << /F1 3 0 R >> >> >>`;
    kids.push(`${pageObjectId} 0 R`);
  }
  objects[2] = `<< /Type /Pages /Kids [${kids.join(" ")}] /Count ${pages.length} >>`;

  const pdf: string[] = ["%PDF-1.4\n"];
  const offsets: Record<number, number> = {};
  let cursor = pdf[0].length;
  const objectIds = Object.keys(objects).map(Number).sort((a, b) => a - b);
  for (const objectId of objectIds) {
    offsets[objectId] = cursor;
    pdf.push(`${objectId} 0 obj\n${objects[objectId]}\nendobj\n`);
    cursor += pdf[pdf.length - 1].length;
  }
  const xrefOffset = cursor;
  pdf.push(`xref\n0 ${Math.max(1, Math.max(...objectIds)) + 1}\n`);
  pdf.push("0000000000 65535 f \n");
  for (let objectId = 1; objectId <= Math.max(...objectIds); objectId += 1) {
    pdf.push(`${String(offsets[objectId] ?? 0).padStart(10, "0")} 00000 n \n`);
  }
  pdf.push(`trailer\n<< /Size ${Math.max(...objectIds) + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF`);

  return new Blob([pdf.join("")], { type: "application/pdf" });
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

  const doc = document.data;
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, enabled: Boolean(document.data) });
  const summaryStartedRef = useRef(false);
  const [summaryText, setSummaryText] = useState("");
  const [summarySources, setSummarySources] = useState<AskResult["sources"]>([]);
  const summary = useMutation({
    mutationFn: async () => {
      if (!doc) throw new Error("Document is not available.");
      const conversation = await api.createConversation([doc.id], `Simple summary: ${doc.filename}`);
      const response = await api.sendConversationMessageStream(conversation.id, summaryQuestion, requestId(), {
        mode: "local",
        allow_hosted: false,
        allow_hosted_documents: false,
        web_research: false,
      }, (delta) => setSummaryText((current) => current + delta));
      return { response, conversationId: conversation.id };
    },
    onMutate: () => {
      setSummaryText("");
      setSummarySources([]);
    },
    onSuccess: ({ response }) => {
      setSummaryText(response.answer.answer);
      setSummarySources(response.answer.sources ?? []);
      summaryStartedRef.current = false;
      toast.success("AI summary generated.");
    },
    onError: (error) => {
      summaryStartedRef.current = false;
      toast.error(error instanceof Error ? error.message : "Could not generate the document summary.");
    },
  });

  useEffect(() => {
    if (!doc || summaryStartedRef.current || summary.isPending || summaryText) return;
    const readyStatus = ["completed", "needs_review"];
    if (!readyStatus.includes(doc.status)) return;
    summaryStartedRef.current = true;
    void summary.mutate();
  }, [doc, summary, summary.isPending, summaryText]);
  const reviewPdfUrl = useMemo(() => {
    if (!doc) return "";
    const blob = buildReviewPdfBlob(doc.filename, summaryText, summarySources);
    return URL.createObjectURL(blob);
  }, [doc, summaryText, summarySources]);

  useEffect(() => {
    return () => {
      if (reviewPdfUrl.startsWith("blob:")) {
        URL.revokeObjectURL(reviewPdfUrl);
      }
    };
  }, [reviewPdfUrl]);

  if (document.isLoading) return <div className="flex items-center gap-2 py-16 text-xs text-muted"><LoaderCircle className="animate-spin" size={15} />Loading document evidence…</div>;
  if (document.isError || !doc) return <Surface className="p-6"><p className="text-sm font-semibold">Document unavailable</p><p className="mt-2 text-xs text-muted">{document.error instanceof ApiError && document.error.status === 404 ? "It may have been deleted or belong to another account." : document.error?.message}</p><Link className="mt-4 inline-block text-xs text-teal hover:underline" href="/dashboard/documents">Return to my documents</Link></Surface>;

  const downloadReviewPdf = () => {
    if (!doc || !reviewPdfUrl) return;
    const anchor = globalThis.document.createElement("a");
    anchor.href = reviewPdfUrl;
    anchor.download = `${doc.filename.replace(/\.[^/.]+$/, "") || "document-review"}-review.pdf`;
    anchor.click();
  };

  const printReviewPdf = () => {
    if (!doc || !reviewPdfUrl) {
      toast.error("The review PDF is still loading. Please try again in a moment.");
      return;
    }

    const printWindow = globalThis.window.open("", "_blank", "noopener,noreferrer,width=1200,height=900");
    if (!printWindow) {
      toast.error("Please allow pop-ups so the review PDF can be printed.");
      window.open(reviewPdfUrl, "_blank", "noopener,noreferrer");
      return;
    }

    printWindow.document.write(`<!doctype html><html><head><title>${doc.filename} - Review</title><meta charset="utf-8" /><style>html,body{margin:0;height:100%;background:#f8fafc;font-family:Arial,sans-serif}body{display:flex;align-items:center;justify-content:center;padding:16px}embed{width:100%;height:calc(100vh - 32px);border:0;background:#fff}</style></head><body><embed src="${reviewPdfUrl}" type="application/pdf" /></body></html>`);
    printWindow.document.close();
    printWindow.focus();

    const ready = () => {
      try {
        printWindow.focus();
        printWindow.print();
      } catch {
        window.open(reviewPdfUrl, "_blank", "noopener,noreferrer");
      }
    };

    if (printWindow.document.readyState === "complete") {
      ready();
      return;
    }
    printWindow.addEventListener("load", ready, { once: true });
  };

  const exportReview = (format: "markdown" | "html" | "print") => {
    if (!doc) return;
    const baseName = doc.filename.replace(/\.[^/.]+$/, "") || "document-review";
    const details = Object.entries(doc.terms ?? {}).map(([field, value]) => {
      const currentValue = value.effective_value ?? value.value;
      const page = value.page ? `Page ${value.page}` : "Page not stated";
      return `- ${humanize(field)}: ${currentValue ?? "Not found"} (${page})`;
    }).join("\n");
    const bodyText = summaryText || `# ${doc.filename}\n\n## Overview\n${doc.analysis_note || "The document was analyzed and extracted values were saved for review."}\n\n## Key extracted values\n${details || "No extracted values were recorded."}`;
    if (format === "print") {
      printReviewPdf();
      return;
    }
    const extension = format === "html" ? "html" : "md";
    const content = format === "html"
      ? `<!doctype html><html><head><meta charset="utf-8"><title>${doc.filename}</title></head><body><pre>${String(bodyText).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</pre></body></html>`
      : bodyText;
    const blob = new Blob([content], { type: format === "html" ? "text/html;charset=utf-8" : "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = globalThis.document.createElement("a");
    anchor.href = url;
    anchor.download = `${baseName}-review.${extension}`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

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
    <div className="flex flex-wrap items-start justify-between gap-4"><div><Link href="/dashboard/documents" className="inline-flex items-center gap-2 text-xs text-muted hover:text-teal"><ArrowLeft size={14} />My documents</Link><Eyebrow className="mt-5">TEXT EXTRACTED · CLASSIFICATION NEEDED</Eyebrow><h1 className="mt-2 break-words text-2xl font-semibold tracking-tight sm:text-3xl">{doc.filename}</h1><p className="mt-2 text-xs text-muted">{doc.source_locator_type === "section" ? `${doc.section_count ?? 0} text sections · page layout unavailable` : `${doc.page_count} pages`} · Structured analysis unavailable</p></div><div className="flex flex-wrap gap-2"><Button variant="danger" size="sm" disabled={remove.isPending} onClick={() => { if (window.confirm("Delete this document analysis?")) remove.mutate(); }}><Trash2 size={14} />Delete</Button></div></div>
    <VersionHistory id={id} docType={doc.doc_type} />
    <Surface className="border-amber-200 bg-amber-50 p-4 sm:p-5"><p className="text-xs font-semibold text-amber-950">{doc.suggested_doc_type ? `Possible document type: ${humanize(doc.suggested_doc_type)}${doc.suggested_loan_category ? ` · ${humanize(doc.suggested_loan_category)} loan` : ""}` : "This file was not classified as a supported loan document or bank statement."}</p><p className="mt-2 text-xs leading-5 text-amber-900">{doc.analysis_note ?? "Readable text is available, but BankEase did not run structured loan or statement analysis. Check the original pages before relying on any information."}</p></Surface>
    <SourcePreview id={id} filename={doc.filename} available={doc.source_available} />
  </div>;
  return <div className="space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-4"><div><Link href="/dashboard/documents" className="inline-flex items-center gap-2 text-xs text-muted hover:text-teal"><ArrowLeft size={14} />My documents</Link><Eyebrow className="mt-5">DOCUMENT REVIEW · {humanize(doc.doc_type || "Document")}</Eyebrow><h1 className="mt-2 break-words text-2xl font-semibold tracking-tight sm:text-3xl">{doc.filename}</h1><p className="mt-2 text-xs text-muted">{doc.page_count} pages · {humanize(doc.status)}{doc.ocr_pages?.length ? ` · OCR on pages ${doc.ocr_pages.join(", ")} — verify all extracted values against the scan` : ""}{doc.unreadable_pages?.length ? ` · Text unavailable on pages ${doc.unreadable_pages.join(", ")}` : ""}</p></div><div className="flex flex-wrap gap-2"><Button variant="danger" size="sm" disabled={remove.isPending} onClick={() => { if (window.confirm("Delete this document analysis?")) remove.mutate(); }}><Trash2 size={14} />Delete</Button></div></div>

    <VersionHistory id={id} docType={doc.doc_type} />

    <SourcePreview
      id={id}
      filename={doc.filename}
      available={doc.source_available}
      title="Original source"
      subtitle="Private preview available to this account"
      openLabel="Open source"
    />

    {reviewPdfUrl && <SourcePreview
      id={undefined}
      filename={`${doc.filename.replace(/\.[^/.]+$/, "") || "document-review"}-review.pdf`}
      available={Boolean(reviewPdfUrl)}
      previewUrl={reviewPdfUrl}
      downloadUrl={reviewPdfUrl}
      title="Review source"
      subtitle="AI-generated review for this document"
      openLabel="Open review PDF"
      showPrint
      onPrint={printReviewPdf}
      printLabel="Print review"
    />}

    <Surface className="p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Eyebrow>AI SUMMARY</Eyebrow>
          <h2 className="mt-1 text-lg font-semibold">Plain-English document summary</h2>
        </div>
        <Button size="sm" variant="secondary" onClick={() => summary.mutate()} disabled={summary.isPending}>{summary.isPending ? <LoaderCircle className="animate-spin" size={13} /> : <MessageSquareText size={13} />}Refresh summary</Button>
      </div>
      {!summaryText && !summary.isPending && <p className="mt-3 text-xs leading-6 text-muted">This summary is generated from the uploaded document only, in simple language, and it clearly notes what is not stated.</p>}
      {summary.isPending && !summaryText && <p className="mt-3 flex items-center gap-2 text-xs text-muted"><LoaderCircle className="animate-spin" size={14} />Generating the document summary…</p>}
      {summaryText && <div className="mt-4 whitespace-pre-wrap break-words text-xs leading-6 text-ink">{summaryText}</div>}
      {summarySources.length > 0 && <div className="mt-4 rounded-lg border border-line bg-panel-raised p-3"><p className="text-[10px] font-semibold uppercase tracking-[.15em] text-muted">Evidence</p><ul className="mt-2 space-y-2 text-[10px] text-muted">{summarySources.map((source, index) => <li key={`${source.page ?? "page"}-${index}`} className="rounded-md border border-line bg-panel px-2 py-1.5">{source.title ?? source.filename ?? "Source"} · {source.page ? `Page ${source.page}` : "Source evidence"}</li>)}</ul></div>}
    </Surface>

    {summaryText && (() => {
      const summaryQuality = computeSummaryQuality(summaryText, summarySources);
      const scoreTone = summaryQuality.score >= 85 ? "text-teal" : summaryQuality.score >= 70 ? "text-amber-700" : "text-red-600";
      const badgeTone = summaryQuality.score >= 85 ? "border-teal/20 bg-teal/10 text-teal" : summaryQuality.score >= 70 ? "border-amber-200 bg-amber-50 text-amber-700" : "border-red-200 bg-red-50 text-red-700";
      const barTone = summaryQuality.score >= 85 ? "bg-teal" : summaryQuality.score >= 70 ? "bg-amber-500" : "bg-red-500";
      return <Surface className="rounded-2xl border border-slate-200 bg-[#f8fafc] p-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)] sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <Eyebrow>AI SUMMARY QUALITY</Eyebrow>
            <h2 className="mt-1 text-lg font-semibold text-slate-900">Review report</h2>
          </div>
          <div className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[.16em] ${badgeTone}`}>{summaryQuality.label}</div>
        </div>
        <div className="mt-4 grid gap-4 lg:grid-cols-[180px_1fr] lg:items-center">
          <div className="rounded-2xl border border-slate-200 bg-white p-4 text-center shadow-sm">
            <p className="text-[10px] uppercase tracking-[.14em] text-slate-500">Quality score</p>
            <p className={`mt-2 text-4xl font-semibold ${scoreTone}`}>{summaryQuality.score}</p>
          </div>
          <div>
            <div className="h-2.5 overflow-hidden rounded-full bg-slate-200"><div className={`h-full rounded-full ${barTone}`} style={{ width: `${summaryQuality.score}%` }} /></div>
            <p className="mt-3 text-[11px] leading-5 text-slate-600">{summaryQuality.note}</p>
            <ul className="mt-3 space-y-2 text-[11px] text-slate-600">{summaryQuality.checks.map((check) => <li key={check.label} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-2.5 py-1.5"><span>{check.label}</span><span className={check.passed ? "font-semibold text-teal" : "text-slate-400"}>{check.passed ? "Yes" : "No"}</span></li>)}</ul>
          </div>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-3">
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-3"><p className="text-[10px] font-semibold uppercase tracking-[.14em] text-emerald-700">Strengths</p><ul className="mt-2 space-y-2 text-[11px] leading-5 text-emerald-900">{summaryQuality.strengths.map((item) => <li key={item} className="list-disc pl-4">{item}</li>)}</ul></div>
          <div className="rounded-2xl border border-amber-200 bg-amber-50 p-3"><p className="text-[10px] font-semibold uppercase tracking-[.14em] text-amber-700">Risk warnings</p><ul className="mt-2 space-y-2 text-[11px] leading-5 text-amber-900">{summaryQuality.risks.length > 0 ? summaryQuality.risks.map((item) => <li key={item} className="list-disc pl-4">{item}</li>) : <li className="list-disc pl-4">No major risk warning detected.</li>}</ul></div>
          <div className="rounded-2xl border border-sky-200 bg-sky-50 p-3"><p className="text-[10px] font-semibold uppercase tracking-[.14em] text-sky-700">Suggested actions</p><ul className="mt-2 space-y-2 text-[11px] leading-5 text-sky-900">{summaryQuality.actions.map((item) => <li key={item} className="list-disc pl-4">{item}</li>)}</ul></div>
        </div>
      </Surface>;
    })()}

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

    {isLoan && <Surface className="p-5 sm:p-6"><div className="flex items-center justify-between gap-3"><div><Eyebrow>REPAYMENT CHECK</Eyebrow><h2 className="mt-1 text-sm font-semibold">Reducing-balance estimate</h2></div><Button variant="secondary" size="sm" onClick={() => calculate.mutate()} disabled={calculate.isPending}>{calculate.isPending && <LoaderCircle className="animate-spin" size={14} />}Calculate EMI</Button></div><p className="mt-2 text-xs leading-5 text-muted">Uses the current field values, including saved corrections where present. This estimate does not include every lender fee or rate reset.</p>
        {finance && <div className="mt-5 grid grid-cols-2 gap-3">{[["Calculated EMI", formatCurrency(finance.calculated_emi)], ["Stated EMI", formatCurrency(finance.stated_emi)], ["Total repayment", formatCurrency(finance.total_repayment)], ["Estimated interest", formatCurrency(finance.total_interest)]].map(([label, value]) => <div key={label} className="rounded-lg border border-line bg-slate-50 p-3"><p className="text-[10px] text-muted">{label}</p><p className="mt-2 text-sm font-semibold tabular-nums">{value}</p></div>)}{finance.note && <p className="col-span-2 rounded-lg border border-amber/20 bg-amber/5 p-3 text-xs leading-5 text-amber">{finance.note}</p>}<ul className="col-span-2 space-y-1 text-[10px] leading-4 text-muted">{finance.assumptions.map((assumption) => <li key={assumption}>• {assumption}</li>)}</ul></div>}
      </Surface>}

    {!isKyc && !isFinancial && <Surface className="p-5 sm:p-6"><Eyebrow>{isAudit ? "AUDIT REPORT PASSAGES" : isInsurance ? "POLICY CONDITIONS TO REVIEW" : "CLAUSES TO REVIEW"}</Eyebrow><p className="mt-1 text-xs text-muted">Source passages are shown with their page and a neutral note for context.</p>{clauses.isLoading && <p className="mt-4 text-xs text-muted">Loading passages…</p>}{clauses.isError && <p role="alert" className="mt-4 text-xs text-red-700">{clauses.error.message}</p>}{clauses.data?.length === 0 && <p className="mt-4 text-xs text-muted">No matching passages were detected.</p>}<div className="mt-4 grid gap-3 md:grid-cols-2">{clauses.data?.map((clause, index) => <article key={`${clause.page}-${index}`} className="rounded-lg border border-line bg-slate-50 p-4"><div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold">{clause.category}</span><span className="text-[10px] text-teal">Page {clause.page}</span></div><p className="mt-3 text-xs leading-5 text-muted">{clause.text}</p><p className="mt-3 border-t border-line pt-3 text-[11px] leading-5 text-amber">Review note: {clause.attention_point}</p></article>)}</div></Surface>}
  
    <Surface id="review-assistant" className="overflow-hidden p-4 sm:p-5">
      <div className="mb-4 flex items-center gap-2"><MessageSquareText className="text-teal" size={17} /><Eyebrow>AI ASSISTANT</Eyebrow></div>
      <div className="min-h-[26rem] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_20px_rgba(15,23,42,0.04)]">
        {me.isSuccess ? <AssistantPanel userName={me.data?.name ?? "there"} userId={me.data?.id ?? ""} requestedDocumentId={doc.id} /> : <div className="flex min-h-[26rem] items-center justify-center gap-2 text-xs text-muted"><LoaderCircle className="animate-spin" size={14} />Loading document chat…</div>}
      </div>
    </Surface>
</div>;
}
