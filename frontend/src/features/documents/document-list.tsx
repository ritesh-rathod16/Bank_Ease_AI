"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArrowDownToLine, ArrowRight, Check, ChevronLeft, ChevronRight, CircleAlert, Clock3, Eye, FileText, Files, LayoutGrid, LoaderCircle, MessageSquareText, Pencil, Search, Trash2, UploadCloud, X } from "lucide-react";
import { Fragment, useMemo, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { Document360View } from "@/features/documents/document-360-view";
import { api } from "@/lib/api/queries";
import { humanize } from "@/lib/utils";
import { ApiError } from "@/lib/api/client";
import type { DocumentSummary } from "@/types/api";

const PAGE_SIZE = 10;
const assistantDocumentTypes = new Set(["loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure", "insurance_policy", "financial_statement", "audit_report", "bank_statement_pdf", "bank_statement_csv", "bank_statement_xlsx"]);
const statusTabs = [
  { key: "all", label: "All Documents", matches: (doc: DocumentSummary) => Boolean(doc.id) },
  { key: "draft", label: "Draft", matches: (doc: DocumentSummary) => doc.status === "draft" },
  { key: "under_review", label: "Under Review", matches: (doc: DocumentSummary) => doc.status === "needs_review" },
  { key: "approved", label: "Approved", matches: (doc: DocumentSummary) => doc.status === "approved" },
  { key: "completed", label: "Completed", matches: (doc: DocumentSummary) => doc.status === "completed" },
  { key: "archived", label: "Archived", matches: (doc: DocumentSummary) => doc.status === "archived" },
  { key: "processing", label: "In Progress", matches: (doc: DocumentSummary) => doc.status === "processing" },
  { key: "attention", label: "Needs Attention", matches: (doc: DocumentSummary) => doc.status === "failed" },
] as const;
type SortKey = "filename" | "type" | "status" | "uploaded" | "updated";

function canAskAiAbout(doc: DocumentSummary) {
  const supported = assistantDocumentTypes.has(doc.doc_type) || (doc.doc_type === "unknown" && doc.source_format === "docx" && doc.source_locator_type === "section") || (doc.doc_type === "unknown" && doc.source_locator_type === "section");
  const hasSource = doc.page_count > 0 || doc.doc_type.startsWith("bank_statement_") || doc.source_locator_type === "section";
  return supported && hasSource && ["completed", "needs_review"].includes(doc.status);
}

function dateValue(value?: string) {
  if (!value) return "";
  const date = Date.parse(value);
  return Number.isNaN(date) ? "" : new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function DocumentStatus({ status }: { status: string }) {
  const colors = status === "completed" ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-700" : status === "needs_review" ? "border-amber/20 bg-amber/10 text-amber" : status === "failed" ? "border-red-500/20 bg-red-500/10 text-red-700" : "border-line bg-panel-raised text-muted";
  return <span className={`inline-flex whitespace-nowrap rounded-full border px-2 py-1 text-[10px] font-medium ${colors}`}>{humanize(status || "unknown")}</span>;
}

function DocumentStats({ documents, loading, unavailable }: { documents: DocumentSummary[]; loading: boolean; unavailable: boolean }) {
  const cards = [
    { label: "Total Documents", value: documents.length, icon: Files, note: "Saved to your account" },
    { label: "Draft Documents", value: documents.filter((doc) => doc.status === "draft").length, icon: FileText, note: "No draft state is currently recorded" },
    { label: "Under Review", value: documents.filter((doc) => doc.status === "needs_review").length, icon: CircleAlert, note: "Extraction needs verification" },
    { label: "Completed", value: documents.filter((doc) => doc.status === "completed").length, icon: Check, note: "Analysis completed" },
    { label: "Requiring Attention", value: documents.filter((doc) => ["failed", "needs_review"].includes(doc.status)).length, icon: Clock3, note: "Review or retry may be needed" },
  ];
  return <section aria-label="Document statistics" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
    {cards.map(({ label, value, icon: Icon, note }) => <Surface key={label} className="p-4 shadow-sm"><div className="flex items-start justify-between gap-3"><div><p className="text-[10px] font-medium text-muted">{label}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{loading ? "—" : unavailable ? "Unavailable" : value}</p></div><span className="grid size-9 place-items-center rounded-lg bg-blue-50 text-blue-700"><Icon size={16} /></span></div><p className="mt-2 text-[10px] leading-4 text-muted">{loading ? "Loading account records…" : unavailable ? "Could not load document records" : note}</p></Surface>)}
  </section>;
}

function DocumentActions({ document, onRename, onDelete, deleting }: { document: DocumentSummary; onRename: () => void; onDelete: () => void; deleting: boolean }) {
  const previewUrl = `/api/v1/documents/${encodeURIComponent(document.id)}/preview`;
  const downloadUrl = `${previewUrl}?download=true`;
  return <div className="flex flex-wrap items-center gap-1">
    {canAskAiAbout(document) && <Link className="inline-flex min-h-8 items-center gap-1 rounded-md px-2 text-[11px] font-semibold text-blue-700 hover:bg-blue-50" href={`/dashboard/assistant?document_id=${encodeURIComponent(document.id)}`}><MessageSquareText size={13} />Ask AI</Link>}
    <Link className="inline-flex min-h-8 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-muted hover:bg-panel-raised hover:text-ink" href={`/dashboard/documents/${document.id}`}>Open <ArrowRight size={13} /></Link>
    {document.source_available !== false && <>
      <a className="inline-flex min-h-8 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-muted hover:bg-panel-raised hover:text-ink" href={previewUrl} target="_blank" rel="noreferrer" aria-label={`Preview ${document.filename}`}><Eye size={13} />Preview</a>
      <a className="inline-flex min-h-8 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-muted hover:bg-panel-raised hover:text-ink" href={downloadUrl} aria-label={`Download ${document.filename}`}><ArrowDownToLine size={13} />Download</a>
    </>}
    <Button variant="ghost" size="icon" aria-label={`Rename ${document.filename}`} title="Rename document" onClick={onRename}><Pencil size={14} /></Button>
    <Button variant="ghost" size="icon" aria-label={`Delete ${document.filename}`} title="Delete document" disabled={deleting} onClick={onDelete}><Trash2 size={14} className="text-muted hover:text-red-700" /></Button>
  </div>;
}

export function DocumentList({ initialView = "all" }: { initialView?: "all" | "upload" }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState<(typeof statusTabs)[number]["key"]>("all");
  const [view, setView] = useState<"documents" | "360">("documents");
  const [sortKey, setSortKey] = useState<SortKey>("updated");
  const [sortDescending, setSortDescending] = useState(true);
  const [page, setPage] = useState(1);
  const [selected360Id, setSelected360Id] = useState("");
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
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
            : "Document analyzed successfully.");
      router.push(`/dashboard/documents/${document.id}`);
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not analyze this document."),
  });
  const remove = useMutation({
    mutationFn: api.removeDocument,
    onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["documents"] }), queryClient.invalidateQueries({ queryKey: ["conversations"] }), queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] })]); queryClient.removeQueries({ queryKey: ["conversation-messages"] }); toast.success("Document deleted."); },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Could not delete document."),
  });
  const rename = useMutation({
    mutationFn: ({ id, filename }: { id: string; filename: string }) => api.renameDocument(id, filename),
    onSuccess: async (renamed) => {
      setRenameId(null);
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["documents"] }), queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] }), queryClient.invalidateQueries({ queryKey: ["document", renamed.id] })]);
      toast.success("Document renamed.");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Could not rename document."),
  });

  const activeStatus = statusTabs.find((tab) => tab.key === activeTab) ?? statusTabs[0];
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    const rows = (documents.data ?? []).filter((doc) => {
      const text = [doc.filename, doc.doc_type, doc.loan_category ?? "", doc.status].join(" ").toLocaleLowerCase();
      return activeStatus.matches(doc) && (!query || text.includes(query));
    });
    const getValue = (doc: DocumentSummary) => sortKey === "filename" ? doc.filename.toLocaleLowerCase()
      : sortKey === "type" ? (doc.suggested_doc_type || doc.doc_type).toLocaleLowerCase()
        : sortKey === "status" ? doc.status.toLocaleLowerCase()
          : Date.parse((sortKey === "uploaded" ? doc.created_at : doc.updated_at) ?? "") || 0;
    rows.sort((left, right) => {
      const a = getValue(left); const b = getValue(right);
      const compare = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
      return sortDescending ? -compare : compare;
    });
    return rows;
  }, [documents.data, activeStatus, search, sortKey, sortDescending]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const selectedDocumentId = selected360Id && documents.data?.some((doc) => doc.id === selected360Id) ? selected360Id : documents.data?.[0]?.id ?? "";

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) upload.mutate(file);
  }

  function selectTab(tab: (typeof statusTabs)[number]["key"]) {
    setActiveTab(tab);
    setPage(1);
  }

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortDescending((value) => !value);
    else { setSortKey(key); setSortDescending(key === "uploaded" || key === "updated"); }
  }

  function startRename(doc: DocumentSummary) {
    setRenameId(doc.id);
    setRenameValue(doc.filename);
  }

  function submitRename(event: FormEvent<HTMLFormElement>, id: string) {
    event.preventDefault();
    const filename = renameValue.trim();
    if (!filename) { toast.error("Enter a document name."); return; }
    if (filename.length > 120) { toast.error("Document names must be 120 characters or fewer."); return; }
    rename.mutate({ id, filename });
  }

  const lifecycleNote = "The stored workflow currently supports In Progress, Under Review, Completed, and Needs Attention. Draft, Approved, and Archived are filterable when records with those actual states exist; the app does not create those states yet.";

  if (initialView === "upload") {
    const handleDrop = (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      const file = event.dataTransfer.files?.[0];
      if (file) upload.mutate(file);
    };

    return <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4"><div><Eyebrow>SECURE DOCUMENT INTAKE</Eyebrow><h1 className="mt-2 text-3xl font-semibold tracking-[-.04em]">Upload a document</h1><p className="mt-2 text-sm text-muted">Drag and drop a PDF, image, Word file, text file, or bank statement. Only file intake is kept on this page.</p></div></div>
      <Surface className="p-5 sm:p-6">
        <div
          onDragOver={(event) => event.preventDefault()}
          onDrop={handleDrop}
          className="grid min-h-[280px] place-items-center rounded-2xl border-2 border-dashed border-line bg-panel-raised p-6 text-center"
        >
          <div className="max-w-lg space-y-4">
            <span className="mx-auto grid size-14 place-items-center rounded-2xl bg-blue-50 text-blue-700"><UploadCloud size={24} /></span>
            <div>
              <p className="text-lg font-semibold">Drop file here</p>
              <p className="mt-1 text-xs text-muted">or choose a file to begin analysis.</p>
            </div>
            <label className={`inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700 ${upload.isPending ? "pointer-events-none opacity-50" : ""}`}>
              <input type="file" className="sr-only" accept="application/pdf,.pdf,text/csv,.csv,text/plain,.txt,application/json,.json,.jsonl,image/png,.png,image/jpeg,.jpg,.jpeg,image/tiff,.tif,.tiff,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.xlsx,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx" onChange={onFile} disabled={upload.isPending} />
              {upload.isPending ? "Analysis queued…" : "Choose a file"}
            </label>
          </div>
        </div>
      </Surface>
    </div>;
  }

  return <div className="space-y-6">
    <div className="flex flex-wrap items-end justify-between gap-4"><div><Eyebrow>DOCUMENT LIBRARY</Eyebrow><h1 className="mt-2 text-3xl font-semibold tracking-[-.04em]">My documents</h1><p className="mt-2 text-sm text-muted">Manage your private analyses, inspect source files, and review extracted information.</p></div><label className={`inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700 ${upload.isPending ? "pointer-events-none opacity-50" : ""}`}><input type="file" className="sr-only" accept="application/pdf,.pdf,text/csv,.csv,text/plain,.txt,application/json,.json,.jsonl,image/png,.png,image/jpeg,.jpg,.jpeg,image/tiff,.tif,.tiff,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.xlsx,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx" onChange={onFile} disabled={upload.isPending} /><UploadCloud size={16} />{upload.isPending ? "Analysis queued…" : "Analyze a document"}</label></div>

    <DocumentStats documents={documents.data ?? []} loading={documents.isLoading} unavailable={documents.isError} />

    <div className="flex flex-wrap gap-2 border-b border-line" role="tablist" aria-label="Document workspace view">
      <button type="button" role="tab" aria-selected={view === "documents"} onClick={() => setView("documents")} className={`inline-flex min-h-10 items-center gap-2 border-b-2 px-3 text-xs font-semibold ${view === "documents" ? "border-teal text-ink" : "border-transparent text-muted hover:text-ink"}`}><LayoutGrid size={14} />Documents</button>
      <button type="button" role="tab" aria-selected={view === "360"} onClick={() => setView("360")} className={`inline-flex min-h-10 items-center gap-2 border-b-2 px-3 text-xs font-semibold ${view === "360" ? "border-teal text-ink" : "border-transparent text-muted hover:text-ink"}`}><Archive size={14} />360° View</button>
    </div>

    {view === "360" && <Document360View documents={documents.data ?? []} selectedId={selectedDocumentId} onSelect={setSelected360Id} />}

    {view === "documents" && <Surface className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line p-4 sm:px-5"><div><p className="text-xs font-semibold">Document workspace <span className="ml-1 text-muted">{documents.data?.length ?? "—"}</span></p><p className="mt-1 text-[10px] text-muted">Search, sort, filter, preview, rename, download, or open a full analysis.</p></div><label className="relative w-full sm:w-72"><Search className="absolute left-3 top-2.5 text-muted" size={15} /><Input className="pl-9" value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Search names, types, status" aria-label="Search documents" /></label></div>
      <div className="flex gap-2 overflow-x-auto border-b border-line px-4 pt-3 sm:px-5" role="tablist" aria-label="Filter documents by status">{statusTabs.map((tab) => {
        const count = (documents.data ?? []).filter(tab.matches).length;
        return <button key={tab.key} type="button" role="tab" aria-selected={activeTab === tab.key} onClick={() => selectTab(tab.key)} className={`inline-flex min-h-9 shrink-0 items-center gap-2 rounded-t-lg border border-b-0 px-3 text-[11px] font-semibold ${activeTab === tab.key ? "border-line bg-panel text-ink" : "border-transparent text-muted hover:bg-panel-raised hover:text-ink"}`}>{tab.label}<span className="rounded-full bg-panel-raised px-1.5 py-0.5 text-[9px] tabular-nums">{count}</span></button>;
      })}</div>
      <p className="border-b border-line bg-panel-raised/50 px-4 py-2 text-[10px] leading-4 text-muted sm:px-5">{lifecycleNote}</p>

      {documents.isLoading && <div className="flex items-center gap-2 p-6 text-xs text-muted"><LoaderCircle className="animate-spin" size={15} />Loading your documents…</div>}
      {documents.isError && <div role="alert" className="p-6 text-xs text-red-700">{documents.error.message}<Button variant="secondary" size="sm" className="ml-3" onClick={() => void documents.refetch()}>Try again</Button></div>}
      {documents.isSuccess && documents.data.length === 0 && <div className="grid justify-items-center px-6 py-12 text-center"><span className="grid size-12 place-items-center rounded-xl bg-blue-50 text-teal"><FileText size={22} /></span><p className="mt-4 text-sm font-semibold">No documents yet</p><p className="mt-2 max-w-sm text-xs leading-5 text-muted">Upload a readable PDF, text file, spreadsheet, or scanned image to create your first private analysis.</p></div>}
      {documents.isSuccess && documents.data.length > 0 && filtered.length === 0 && <div className="grid justify-items-center px-6 py-12 text-center"><span className="grid size-10 place-items-center rounded-lg bg-panel-raised text-muted"><Search size={17} /></span><p className="mt-3 text-xs font-semibold">No matching documents</p><p className="mt-1 max-w-md text-[10px] leading-5 text-muted">{activeTab !== "all" && ["draft", "approved", "archived"].includes(activeTab) ? `No documents have the ${activeStatus.label.toLowerCase()} state. ${lifecycleNote}` : search ? `No documents match “${search}”.` : "There are no documents in this status."}</p></div>}

      {filtered.length > 0 && <>
        <div className="hidden overflow-x-auto xl:block"><table className="w-full min-w-[980px] text-left text-xs"><thead className="bg-panel-raised text-[10px] uppercase tracking-wider text-muted"><tr>
          {([["filename", "Document Name"], ["type", "Document Type"], ["status", "Status"], ["uploaded", "Uploaded Date"], ["updated", "Last Updated"]] as [SortKey, string][]).map(([key, label]) => <th key={key} className="px-4 py-3 font-semibold first:pl-5"><button type="button" onClick={() => toggleSort(key)} className="inline-flex items-center gap-1 hover:text-ink">{label}{sortKey === key && <span aria-label={sortDescending ? "sorted descending" : "sorted ascending"}>{sortDescending ? "↓" : "↑"}</span>}</button></th>)}<th className="px-4 py-3 text-right font-semibold">Actions</th></tr></thead><tbody className="divide-y divide-line">{visible.map((doc) => <Fragment key={doc.id}>
            <tr className="align-middle hover:bg-panel-raised/50"><td className="max-w-[260px] px-5 py-4"><Link className="flex items-center gap-3 font-medium hover:text-teal" href={`/dashboard/documents/${doc.id}`}><span className="grid size-8 shrink-0 place-items-center rounded-lg border border-line bg-panel-raised text-muted"><FileText size={15} /></span><span className="truncate">{doc.filename}</span></Link></td><td className="max-w-[180px] px-4 py-4 text-muted">{humanize(doc.suggested_doc_type || doc.doc_type || "unknown")}{doc.suggested_doc_type && <span className="mt-1 block text-[10px] text-amber">Suggested type · review</span>}</td><td className="px-4 py-4"><DocumentStatus status={doc.status} /></td><td className="whitespace-nowrap px-4 py-4 text-muted">{dateValue(doc.created_at) || "—"}</td><td className="whitespace-nowrap px-4 py-4 text-muted">{dateValue(doc.updated_at) || "—"}</td><td className="px-4 py-4"><div className="flex justify-end"><DocumentActions document={doc} onRename={() => startRename(doc)} onDelete={() => { if (window.confirm(`Delete ${doc.filename}? This removes its stored analysis.`)) remove.mutate(doc.id); }} deleting={remove.isPending} /></div></td></tr>
            {renameId === doc.id && <tr><td colSpan={6} className="bg-panel-raised/60 px-5 py-3"><form className="flex flex-wrap items-end gap-2" onSubmit={(event) => submitRename(event, doc.id)}><label className="grid min-w-[min(100%,22rem)] flex-1 gap-1 text-[10px] text-muted">New filename <Input autoFocus maxLength={120} value={renameValue} onChange={(event) => setRenameValue(event.target.value)} /></label><Button type="submit" size="sm" disabled={rename.isPending}>{rename.isPending ? <LoaderCircle className="animate-spin" size={13} /> : <Check size={13} />}Save name</Button><Button type="button" size="sm" variant="ghost" onClick={() => setRenameId(null)}><X size={13} />Cancel</Button></form></td></tr>}
          </Fragment>)}</tbody></table></div>

        <div className="space-y-3 p-3 xl:hidden">{visible.map((doc) => <article key={doc.id} className="rounded-lg border border-line bg-panel p-3">
          <div className="flex items-start justify-between gap-3"><Link href={`/dashboard/documents/${doc.id}`} className="flex min-w-0 items-center gap-2 font-medium hover:text-teal"><span className="grid size-8 shrink-0 place-items-center rounded-lg border border-line bg-panel-raised text-muted"><FileText size={14} /></span><span className="break-words text-xs">{doc.filename}</span></Link><DocumentStatus status={doc.status} /></div>
          <p className="mt-2 text-[10px] text-muted">{humanize(doc.suggested_doc_type || doc.doc_type || "unknown")} · Uploaded {dateValue(doc.created_at) || "date not recorded"}</p><p className="mt-1 text-[10px] text-muted">Last updated {dateValue(doc.updated_at) || "date not recorded"}</p>
          <div className="mt-2 border-t border-line pt-2"><DocumentActions document={doc} onRename={() => startRename(doc)} onDelete={() => { if (window.confirm(`Delete ${doc.filename}? This removes its stored analysis.`)) remove.mutate(doc.id); }} deleting={remove.isPending} /></div>
          {renameId === doc.id && <form className="mt-3 grid gap-2 border-t border-line pt-3" onSubmit={(event) => submitRename(event, doc.id)}><label className="grid gap-1 text-[10px] text-muted">New filename <Input autoFocus maxLength={120} value={renameValue} onChange={(event) => setRenameValue(event.target.value)} /></label><div className="flex gap-2"><Button type="submit" size="sm" disabled={rename.isPending}>{rename.isPending ? <LoaderCircle className="animate-spin" size={13} /> : <Check size={13} />}Save</Button><Button type="button" size="sm" variant="ghost" onClick={() => setRenameId(null)}><X size={13} />Cancel</Button></div></form>}
        </article>)}</div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3 text-[10px] text-muted sm:px-5"><p>Showing {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filtered.length)} of {filtered.length}</p><div className="flex items-center gap-2"><Button variant="secondary" size="sm" disabled={currentPage <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}><ChevronLeft size={13} />Previous</Button><span className="tabular-nums">Page {currentPage} of {pageCount}</span><Button variant="secondary" size="sm" disabled={currentPage >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>Next<ChevronRight size={13} /></Button></div></div>
      </>}
    </Surface>}

    <p className="text-[11px] leading-5 text-muted">Original files are kept in private local storage. Document previews and downloads require your signed-in account. Local development records are account-scoped but are not encrypted at rest.</p>
  </div>;
}
