"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, ExternalLink, LoaderCircle, MessageSquareText, Trash2 } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import type { DocumentRecord } from "@/types/api";
import { humanize } from "@/lib/utils";
import { SourcePreview } from "@/features/documents/source-preview";
import { StatementTransactionRow } from "@/features/documents/statement-transaction-row";
import type { TransactionCorrectionRequest } from "@/types/api";

const PAGE_SIZE = 100;

function money(minor: number | null, currency: string) {
  if (minor === null) return "Not available";
  const amount = minor / 100;
  if (currency === "UNSPECIFIED") {
    return amount.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  try {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency }).format(amount);
  } catch {
    return `${currency} ${amount.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
}

export function StatementWorkspace({
  id,
  document,
  onDelete,
  deleting,
}: Readonly<{
  id: string;
  document: DocumentRecord;
  onDelete: () => void;
  deleting: boolean;
}>) {
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const queryClient = useQueryClient();
  const invalidDateRange = Boolean(dateFrom && dateTo && dateFrom > dateTo);
  useEffect(() => {
    const timer = window.setTimeout(() => setSearchQuery(search), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  const page = useQuery({
    queryKey: ["transactions", id, offset, searchQuery, dateFrom, dateTo],
    queryFn: () => api.transactions(id, offset, PAGE_SIZE, { search: searchQuery, date_from: dateFrom, date_to: dateTo }),
    enabled: !invalidDateRange,
  });
  const correction = useMutation({
    mutationFn: ({ rowNumber, body }: { rowNumber: number; body: TransactionCorrectionRequest }) =>
      api.correctTransaction(id, rowNumber, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["transactions", id] });
      toast.success("Correction saved. The imported row and evidence remain unchanged.");
    },
  });
  const requestRowReview = useMutation({
    mutationFn: (rowNumber: number) => api.requestReview(id, `transaction:${rowNumber}`, `Please verify statement row ${rowNumber} against its source evidence.`),
    onSuccess: async (task) => {
      await queryClient.invalidateQueries({ queryKey: ["reviews"] });
      toast.success(task.created ? "Statement row review requested." : "A review is already open for this row.");
    },
    onError: (error) => toast.error(error.message),
  });
  const analysis = page.data?.statement_analysis ?? document.statement_analysis;
  const currency = analysis?.currency ?? document.currency ?? "UNSPECIFIED";
  const totalPages = Math.max(1, Math.ceil((page.data?.total ?? document.transaction_count ?? 0) / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return <div className="space-y-6">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <Link href="/dashboard/documents" className="inline-flex items-center gap-2 text-xs text-muted hover:text-teal"><ArrowLeft size={14} />My documents</Link>
        <Eyebrow className="mt-5">BANK STATEMENT · {document.doc_type === "bank_statement_pdf" ? "PDF" : document.doc_type === "bank_statement_xlsx" ? "XLSX" : "CSV"} ANALYSIS</Eyebrow>
        <h1 className="mt-2 break-words text-2xl font-semibold tracking-tight sm:text-3xl">{document.filename}</h1>
        <p className="mt-2 text-xs text-muted">{document.transaction_count ?? page.data?.total ?? 0} extracted rows · {currency} · deterministic checks</p>
      </div>
      <div className="flex gap-2">
        <Link href={`/dashboard/assistant?document_id=${id}`} className="inline-flex min-h-8 items-center gap-2 rounded-lg bg-blue-600 px-3 text-xs font-semibold text-white hover:bg-blue-700"><MessageSquareText size={14} />AI Assistant</Link>
        <a className="inline-flex min-h-8 items-center gap-2 rounded-lg border border-line bg-panel-raised px-3 text-xs font-semibold text-ink hover:border-teal/30 hover:text-teal" href={api.reportUrl(id)} target="_blank" rel="noreferrer"><ExternalLink size={14} />Open summary</a>
        <Button variant="danger" size="sm" disabled={deleting} onClick={onDelete}><Trash2 size={14} />Delete</Button>
      </div>
    </div>

    <SourcePreview id={id} filename={document.filename} available={document.source_available} />

    {!analysis && page.isLoading && <div className="flex items-center gap-2 py-8 text-xs text-muted"><LoaderCircle className="animate-spin" size={15} />Loading statement checks…</div>}
    {page.isError && <Surface role="alert" className="p-5 text-sm text-red-700">{page.error.message}</Surface>}

    {analysis && <>
      <section aria-label="Statement summary" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Transactions", String(analysis.transaction_count)],
          ["Balance check", humanize(analysis.balance_check)],
          ["Implied opening balance", money(analysis.opening_balance_minor, currency)],
          ["Closing balance", money(analysis.closing_balance_minor, currency)],
        ].map(([label, value]) => <Surface key={label} className="p-4"><p className="text-[10px] uppercase tracking-wider text-muted">{label}</p><p className={`mt-2 text-sm font-semibold tabular-nums ${label === "Balance check" && analysis.balance_check === "inconsistent" ? "text-amber" : "text-ink"}`}>{value}</p></Surface>)}
      </section>
      <p className="text-[11px] leading-5 text-muted">Balance reconciliation reflects the imported source rows. Saved row corrections are used by new transaction comparisons; they do not rewrite the original reconciliation.</p>

      {analysis.balance_check === "inconsistent" && <Surface className="border-amber-200 p-5">
        <Eyebrow className="text-amber">BALANCE DIFFERENCES TO REVIEW</Eyebrow>
        <p className="mt-2 text-xs leading-5 text-muted">A difference is a review signal. Check the source statement; it does not establish an error or misconduct.</p>
        <ul className="mt-4 space-y-2 text-xs">{analysis.balance_issues.map((issue) => <li key={issue.row_number} className="rounded-lg border border-line bg-slate-50 p-3">Source row {issue.row_number}: expected {money(issue.expected_minor, currency)}, observed {money(issue.observed_minor, currency)} (difference {money(issue.difference_minor, currency)}).</li>)}</ul>
      </Surface>}

      {analysis.duplicate_candidates.length > 0 && <Surface className="p-5">
        <Eyebrow className="text-amber">POSSIBLE DUPLICATE TRANSACTIONS</Eyebrow>
        <p className="mt-2 text-xs leading-5 text-muted">These rows share a date, amount and normalized description. Similarity alone does not mean a charge is duplicated.</p>
        <ul className="mt-4 space-y-2 text-xs">{analysis.duplicate_candidates.map((candidate) => <li key={candidate.rows.join("-")} className="rounded-lg border border-line bg-slate-50 p-3">Rows {candidate.rows.join(", ")} · {candidate.date} · {money(candidate.amount_minor, currency)} · {candidate.description}</li>)}</ul>
      </Surface>}

      {analysis.balance_check === "unavailable" && <Surface className="p-4 text-xs leading-5 text-muted">{analysis.balance_note}</Surface>}

      <Surface className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4"><div><Eyebrow>TRANSACTION ROWS</Eyebrow><p className="mt-1 text-xs text-muted">Corrections are saved separately; original cell values and source evidence stay intact. New comparisons use corrected values.</p></div><p className="text-[11px] text-muted">{invalidDateRange ? "—" : page.data?.total ?? document.transaction_count ?? 0} rows</p></div>
        <div className="grid gap-3 border-b border-line px-5 py-4 sm:grid-cols-2 xl:grid-cols-[minmax(220px,1fr)_180px_180px_auto] xl:items-end">
          <label className="grid gap-1.5 text-[10px] font-medium text-muted">Search transactions<Input type="search" placeholder="Description, date, or source value" value={search} onChange={(event) => { setSearch(event.target.value.slice(0, 100)); setOffset(0); }} /></label>
          <label className="grid gap-1.5 text-[10px] font-medium text-muted">From date<Input type="date" value={dateFrom} onChange={(event) => { setDateFrom(event.target.value); setOffset(0); }} /></label>
          <label className="grid gap-1.5 text-[10px] font-medium text-muted">To date<Input type="date" value={dateTo} onChange={(event) => { setDateTo(event.target.value); setOffset(0); }} /></label>
          <Button variant="ghost" size="sm" disabled={!search && !dateFrom && !dateTo} onClick={() => { setSearch(""); setDateFrom(""); setDateTo(""); setOffset(0); }}>Clear filters</Button>
          {invalidDateRange && <p role="alert" className="text-xs text-red-700 sm:col-span-2 xl:col-span-4">The start date must be on or before the end date.</p>}
        </div>
        {page.isLoading && <div className="flex items-center gap-2 p-5 text-xs text-muted"><LoaderCircle className="animate-spin" size={14} />Loading rows…</div>}
        {page.data && !invalidDateRange && <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-muted">
                <tr><th className="px-4 py-3">Source</th><th className="px-4 py-3">Date</th><th className="px-4 py-3">Description and evidence</th><th className="px-4 py-3 text-right">Debit</th><th className="px-4 py-3 text-right">Credit</th><th className="px-4 py-3 text-right">Balance</th><th className="px-3 py-3 text-right">Actions</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {page.data.transactions.length === 0 && <tr><td colSpan={7} className="px-4 py-8 text-center text-xs text-muted">No transactions were extracted. Review the statement note above and check the source document.</td></tr>}
                {page.data.transactions.map((row) => <StatementTransactionRow key={row.row_number} row={row} currency={currency} saving={correction.isPending} onSave={(rowNumber, body) => correction.mutateAsync({ rowNumber, body })} reviewing={requestRowReview.isPending} onReview={(rowNumber) => requestRowReview.mutateAsync(rowNumber)} />)}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t border-line px-4 py-3"><Button variant="ghost" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}><ArrowLeft size={14} />Previous</Button><p className="text-[10px] text-muted">Page {currentPage} of {totalPages}</p><Button variant="ghost" size="sm" disabled={currentPage >= totalPages} onClick={() => setOffset(offset + PAGE_SIZE)}>Next<ArrowRight size={14} /></Button></div>
        </>}
      </Surface>
    </>}
  </div>;
}
