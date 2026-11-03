"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, FileText, LoaderCircle, Search, Sparkles, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import type { DocumentSummary } from "@/types/api";
import { humanize } from "@/lib/utils";

type LibraryKind = "banking" | "insurance" | "contracts" | "identity";
const contractTypes = new Set(["loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure"]);
const financialTypes = new Set(["financial_statement"]);
const auditTypes = new Set(["audit_report"]);
const assistantDocumentTypes = new Set([...contractTypes, "insurance_policy", "financial_statement", "audit_report", "bank_statement_pdf", "bank_statement_csv", "bank_statement_xlsx"]);
const contractSummaryPrompt = "Explain this contract in plain English like you are talking to a regular borrower. Tell me: what this agreement is for, who is involved, how much money is involved, what the borrower must pay, key dates, penalties, risks, and what I should watch out for. If something is not clearly written in the document, say 'not clearly stated in this agreement.' Use only facts from this document, cite the relevant page for each important point, and keep it short and easy to understand.";

function canSummarize(doc: DocumentSummary) {
  const supported = assistantDocumentTypes.has(doc.doc_type) || (doc.doc_type === "unknown" && doc.source_format === "docx" && doc.source_locator_type === "section");
  const hasSource = doc.page_count > 0 || doc.source_locator_type === "section";
  return supported && hasSource && ["completed", "needs_review"].includes(doc.status);
}

function summaryHref(documentId: string) {
  const params = new URLSearchParams({ document_id: documentId, ask: contractSummaryPrompt });
  return `/dashboard/assistant?${params.toString()}`;
}
function includesDocument(doc: DocumentSummary, kind: LibraryKind, filter: string) {
  const type = doc.doc_type.toLowerCase();
  const suggested = (doc.suggested_doc_type ?? "").toLowerCase();
  const name = doc.filename.toLowerCase();
  if (kind === "insurance") return type.includes("insurance") || suggested.includes("insurance") || name.includes("insurance");
  if (kind === "identity") return type === "kyc_form";
  if (kind === "contracts") return contractTypes.has(type) || contractTypes.has(suggested);
  if (kind === "banking" && filter === "financial") return financialTypes.has(type);
  if (kind === "banking" && filter === "audit") return auditTypes.has(type);
  if (filter === "loans") return contractTypes.has(type) || contractTypes.has(suggested) || doc.loan_category !== null;
  if (filter === "statements") return type.startsWith("bank_statement_");
  return type.startsWith("bank_statement_") || contractTypes.has(type) || contractTypes.has(suggested) || financialTypes.has(type) || auditTypes.has(type) || doc.loan_category !== null;
}

export function DocumentLibrary({ kind, initialFilter = "" }: { kind: LibraryKind; initialFilter?: string }) {
  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents });
  const filter = initialFilter;
  const [search, setSearch] = useState("");
  const titles = { banking: "Banking documents", insurance: "Insurance documents", contracts: "Contract explainer", identity: "KYC forms" };
  const matched = useMemo(() => (documents.data ?? []).filter((doc) => includesDocument(doc, kind, filter) && `${doc.filename} ${doc.doc_type} ${doc.suggested_doc_type ?? ""} ${doc.loan_category ?? ""}`.toLowerCase().includes(search.trim().toLowerCase())), [documents.data, kind, filter, search]);
  return <div className="space-y-5">
    <section className="flex flex-wrap items-end justify-between gap-4"><div><Eyebrow>DOCUMENT LIBRARY</Eyebrow><h1 className="mt-2 text-2xl font-semibold tracking-tight">{titles[kind]}</h1><p className="mt-1 max-w-2xl text-sm leading-6 text-muted">{kind === "insurance" ? "Review labeled policy details and conditions with their source pages. Extraction does not verify active coverage or claim eligibility." : kind === "identity" ? "Review explicitly labeled name, birth date, and address fields from KYC forms. Identity numbers are not extracted and this does not verify identity." : kind === "contracts" ? "Open a contract to review its evidence, or ask AI for a concise plain-language summary with page citations." : "Open an analyzed document to view its extracted values, clauses, comparison, and cited source evidence."}</p></div><Link href="/dashboard/documents" className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-blue-600 px-4 text-xs font-semibold text-white hover:bg-blue-700"><UploadCloud size={15} />Upload document</Link></section>
    {kind === "banking" && <div className="flex flex-wrap gap-2"><Link href="/dashboard/library/banking" className={`rounded-full border px-3 py-1.5 text-[10px] font-semibold ${!filter ? "border-blue-600 bg-blue-600 text-white" : "border-line bg-white text-slate-600"}`}>All banking</Link><Link href="/dashboard/library/banking?type=loans" className={`rounded-full border px-3 py-1.5 text-[10px] font-semibold ${filter === "loans" ? "border-blue-600 bg-blue-600 text-white" : "border-line bg-white text-slate-600"}`}>Loan documents</Link><Link href="/dashboard/library/banking?type=statements" className={`rounded-full border px-3 py-1.5 text-[10px] font-semibold ${filter === "statements" ? "border-blue-600 bg-blue-600 text-white" : "border-line bg-white text-slate-600"}`}>Statements</Link><Link href="/dashboard/library/banking?type=financial" className={`rounded-full border px-3 py-1.5 text-[10px] font-semibold ${filter === "financial" ? "border-blue-600 bg-blue-600 text-white" : "border-line bg-white text-slate-600"}`}>Financial statements</Link><Link href="/dashboard/library/banking?type=audit" className={`rounded-full border px-3 py-1.5 text-[10px] font-semibold ${filter === "audit" ? "border-blue-600 bg-blue-600 text-white" : "border-line bg-white text-slate-600"}`}>Audit reports</Link></div>}
    <Surface className="overflow-hidden shadow-sm"><div className="flex flex-wrap items-center justify-between gap-3 border-b border-line p-4"><p className="text-xs font-semibold">{matched.length} document{matched.length === 1 ? "" : "s"}</p><label className="relative w-full sm:w-72"><Search className="absolute left-3 top-2.5 text-muted" size={14} /><Input value={search} onChange={(event) => setSearch(event.target.value)} className="pl-9" placeholder="Filter by filename or type" /></label></div>
      {documents.isLoading && <div className="flex items-center gap-2 p-5 text-xs text-muted"><LoaderCircle size={14} className="animate-spin" />Loading documents…</div>}
      {documents.isError && <div className="p-5"><p role="alert" className="text-xs text-red-700">{documents.error.message}</p><Button className="mt-3" size="sm" variant="secondary" onClick={() => void documents.refetch()}>Try again</Button></div>}
      {documents.isSuccess && matched.length === 0 && <div className="grid justify-items-center px-6 py-12 text-center"><span className="grid size-11 place-items-center rounded-xl bg-blue-50 text-blue-700"><FileText size={20} /></span><p className="mt-3 text-sm font-semibold">No documents in this view</p><p className="mt-1 max-w-md text-xs leading-5 text-muted">{kind === "insurance" ? "Upload an insurance policy schedule or certificate to extract labeled policy values and review conditions." : kind === "identity" ? "Upload a clearly titled KYC or customer-identification form with labeled fields. Supported PDFs only." : search ? "No documents match this filter." : "Upload a supported PDF or bank statement CSV to begin."}</p><Link href="/dashboard/documents" className="mt-4 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white"><UploadCloud size={14} />Analyze a document</Link></div>}
      {matched.length > 0 && <div className="divide-y divide-line">{matched.map((doc) => <div key={doc.id} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center"><Link href={`/dashboard/documents/${doc.id}`} className="flex min-w-0 flex-1 items-center gap-3 rounded-lg hover:text-teal"><span className="grid size-9 shrink-0 place-items-center rounded-lg border border-line bg-white text-blue-700"><FileText size={16} /></span><span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold">{doc.filename}</span><span className="mt-1 block text-[10px] text-muted">{doc.suggested_doc_type ? `Suggested: ${humanize(doc.suggested_doc_type)}${doc.suggested_loan_category ? ` · ${humanize(doc.suggested_loan_category)} loan` : ""} · Needs review` : humanize(doc.loan_category || doc.doc_type)} · {doc.status === "completed" ? "Analysis complete" : humanize(doc.status)}</span></span><span className="shrink-0 text-[10px] text-slate-500">{doc.doc_type.startsWith("bank_statement_") ? `${doc.transaction_count ?? 0} rows` : doc.source_locator_type === "section" ? `${doc.section_count ?? 0} text sections` : `${doc.page_count} pages`}</span><ArrowUpRight size={15} className="shrink-0 text-slate-400" /></Link>{kind === "contracts" && canSummarize(doc) && <Link href={summaryHref(doc.id)} className="inline-flex min-h-9 shrink-0 items-center justify-center gap-2 rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 text-[11px] font-semibold text-blue-700 transition hover:border-blue-500/50 hover:bg-blue-500/15"><Sparkles size={14} />Summarize in simple words</Link>}</div>)}</div>}
    </Surface>
  </div>;
}
