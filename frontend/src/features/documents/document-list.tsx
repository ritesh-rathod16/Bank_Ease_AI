"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, FileText, LoaderCircle, MessageSquareText, Search, Trash2, UploadCloud } from "lucide-react";
import { useMemo, useState, type ChangeEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import { humanize } from "@/lib/utils";
import { ApiError } from "@/lib/api/client";
import type { DocumentSummary } from "@/types/api";

const assistantDocumentTypes = new Set(["loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure", "insurance_policy", "financial_statement", "audit_report", "bank_statement_pdf", "bank_statement_csv", "bank_statement_xlsx"]);

function canAskAiAbout(doc: DocumentSummary) {
  const supported = assistantDocumentTypes.has(doc.doc_type) || (doc.doc_type === "unknown" && doc.source_format === "docx" && doc.source_locator_type === "section");
  const hasSource = doc.page_count > 0 || doc.doc_type.startsWith("bank_statement_") || doc.source_locator_type === "section";
  return supported && hasSource && ["completed", "needs_review"].includes(doc.status);
}

export function DocumentList({ initialView = "all" }: { initialView?: "all" | "upload" }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents,
    refetchInterval: (query) => query.state.data?.some((document) => document.status === "processing") ? 1500 : false });
  const upload = useMutation({
    mutationFn: api.upload,
    onSuccess: async (document) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["documents"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] }),
      ]);
      toast.success(document.status === "processing" ? "Upload received. Analysis will continue in the background."
        : document.doc_type.startsWith("bank_statement_") ? "Statement imported successfully."
          : document.source_format === "docx" ? "Word text extracted. Review the suggested document type and source text."
            : "PDF analyzed successfully.");
      router.push(`/dashboard/documents/${document.id}`);
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not analyze this document."),
  });
  const remove = useMutation({
    mutationFn: api.removeDocument,
    onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["documents"] }), queryClient.invalidateQueries({ queryKey: ["conversations"] }), queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] })]); queryClient.removeQueries({ queryKey: ["conversation-messages"] }); toast.success("Document deleted."); },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Could not delete document."),
  });
  const filtered = useMemo(() => (documents.data ?? []).filter((doc) => doc.filename.toLowerCase().includes(search.trim().toLowerCase())), [documents.data, search]);

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) upload.mutate(file);
  }

  return <div className="space-y-6">
    <div className="flex flex-wrap items-end justify-between gap-4"><div><Eyebrow>{initialView === "upload" ? "SECURE DOCUMENT INTAKE" : "DOCUMENT LIBRARY"}</Eyebrow><h1 className="mt-2 text-3xl font-semibold tracking-[-.04em]">{initialView === "upload" ? "Upload a document" : "My documents"}</h1><p className="mt-2 text-sm text-muted">{initialView === "upload" ? "Choose a PDF, scanned image, Word document, or bank statement CSV/XLSX. Your private analysis will appear here when processing finishes." : "Your analyses are scoped to your account."}</p></div><label className={`inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700 ${upload.isPending ? "pointer-events-none opacity-50" : ""}`}><input type="file" className="sr-only" accept="application/pdf,.pdf,text/csv,.csv,image/png,.png,image/jpeg,.jpg,.jpeg,image/tiff,.tif,.tiff,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.xlsx,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx" onChange={onFile} disabled={upload.isPending} /><UploadCloud size={16} />{upload.isPending ? "Analysis queued…" : initialView === "upload" ? "Choose a file" : "Analyze a document"}</label></div>
    <Surface className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line p-4 sm:px-5"><p className="text-xs font-semibold">Stored analyses <span className="ml-1 text-muted">{documents.data?.length ?? "—"}</span></p><label className="relative w-full sm:w-64"><Search className="absolute left-3 top-2.5 text-muted" size={15} /><Input className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search filenames" aria-label="Search documents" /></label></div>
      {documents.isLoading && <div className="flex items-center gap-2 p-6 text-xs text-muted"><LoaderCircle className="animate-spin" size={15} />Loading your documents…</div>}
      {documents.isError && <div role="alert" className="p-6 text-xs text-red-700">{documents.error.message}<Button variant="secondary" size="sm" className="ml-3" onClick={() => void documents.refetch()}>Try again</Button></div>}
      {documents.isSuccess && documents.data.length === 0 && <div className="grid justify-items-center px-6 py-12 text-center"><span className="grid size-12 place-items-center rounded-xl bg-blue-50 text-teal"><FileText size={22} /></span><p className="mt-4 text-sm font-semibold">No documents yet</p><p className="mt-2 max-w-sm text-xs leading-5 text-muted">Upload a readable PDF, PNG/JPEG/TIFF scan, or supported bank statement CSV/XLSX. Image OCR needs Tesseract installed on the server.</p></div>}
      {documents.isSuccess && documents.data.length > 0 && filtered.length === 0 && <p className="p-6 text-xs text-muted">No filenames match “{search}”.</p>}
      {filtered.length > 0 && <div className="overflow-x-auto"><table className="w-full min-w-[660px] text-left text-xs"><thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-muted"><tr><th className="px-5 py-3 font-semibold">Document</th><th className="px-4 py-3 font-semibold">Type</th><th className="px-4 py-3 font-semibold">Pages / rows</th><th className="px-4 py-3 font-semibold">Status</th><th className="px-4 py-3 text-right font-semibold">Actions</th></tr></thead><tbody className="divide-y divide-line">{filtered.map((doc) => <tr key={doc.id} className="hover:bg-slate-50"><td className="max-w-[290px] px-5 py-4"><Link className="flex items-center gap-3 font-medium hover:text-teal" href={`/dashboard/documents/${doc.id}`}><span className="grid size-8 shrink-0 place-items-center rounded-lg border border-line bg-panel-raised text-muted"><FileText size={15} /></span><span className="truncate">{doc.filename}</span></Link></td><td className="px-4 py-4 text-muted">{doc.suggested_doc_type ? <><span>{humanize(doc.suggested_doc_type)}{doc.suggested_loan_category ? ` · ${humanize(doc.suggested_loan_category)} loan` : ""}</span><span className="mt-1 block text-[10px] text-amber-700">Suggested · needs review</span></> : humanize(doc.loan_category || doc.doc_type || "Unknown")}</td><td className="px-4 py-4 tabular-nums text-muted">{doc.doc_type.startsWith("bank_statement_") ? `${doc.transaction_count ?? 0} rows` : doc.source_locator_type === "section" ? `${doc.section_count ?? 0} text sections` : (doc.page_count ?? "—")}</td><td className="px-4 py-4"><span className="rounded-full border border-teal/20 bg-teal/10 px-2 py-1 text-[10px] text-teal">{humanize(doc.status)}</span></td><td className="px-4 py-4"><div className="flex justify-end gap-1">{canAskAiAbout(doc) && <Link className="inline-flex min-h-8 items-center gap-1 rounded-md px-2 text-xs font-semibold text-blue-700 hover:bg-blue-50" href={`/dashboard/assistant?document_id=${encodeURIComponent(doc.id)}`}><MessageSquareText size={13} />Ask AI</Link>}<Link className="inline-flex min-h-8 items-center gap-1 rounded-md px-3 text-xs text-muted hover:bg-panel-raised hover:text-teal" href={`/dashboard/documents/${doc.id}`}>Open <ArrowUpRight size={13} /></Link><Button variant="ghost" size="icon" aria-label={`Delete ${doc.filename}`} title={`Delete ${doc.filename}`} disabled={remove.isPending} onClick={() => { if (window.confirm(`Delete ${doc.filename}? This removes its stored analysis.`)) remove.mutate(doc.id); }}><Trash2 size={14} className="text-muted hover:text-red-700" /></Button></div></td></tr>)}</tbody></table></div>}
    </Surface>
    <p className="text-[11px] leading-5 text-muted">PDFs and scanned images are parsed in memory; CSV/XLSX statement rows retain source values and row locations for review. Original files are kept in private local storage. Local development records are account-scoped but are not encrypted at rest.</p>
  </div>;
}
