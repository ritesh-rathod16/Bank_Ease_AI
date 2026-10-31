"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, GitCompareArrows, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import { humanize } from "@/lib/utils";
import type { StatementComparisonResult, StatementComparisonSource } from "@/types/api";
import { ComparisonHistory } from "@/features/comparison/comparison-history";

const schema = z.object({
  document_id_a: z.string().min(1, "Choose statement A."),
  document_id_b: z.string().min(1, "Choose statement B."),
}).refine((value) => value.document_id_a !== value.document_id_b, {
  path: ["document_id_b"], message: "Choose a different statement.",
});
type FormData = z.infer<typeof schema>;

function money(minor: number, currency: string) {
  const value = minor / 100;
  if (currency === "UNSPECIFIED") return value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  try { return new Intl.NumberFormat("en-IN", { style: "currency", currency }).format(value); }
  catch { return `${currency} ${value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
}

function SourceEvidence({ source, label }: { source: StatementComparisonSource | null; label: string }) {
  if (!source) return <p className="text-[11px] text-muted">{label}: no matching row.</p>;
  return <div className="rounded-md border border-line bg-white p-3">
    <p className="text-[10px] font-semibold text-teal">{label} · {source.page ? `Page ${source.page}` : source.source_sheet ? `${source.source_sheet}, row ${source.row_number}` : `CSV row ${source.row_number}`}</p>
    {source.evidence && <p className="mt-2 text-[11px] leading-5 text-muted">{source.evidence}</p>}
    {!source.evidence && <dl className="mt-2 grid gap-1">{Object.entries(source.source_values).filter(([, value]) => value).map(([key, value]) => <div key={key} className="grid grid-cols-[auto_1fr] gap-2 text-[10px]"><dt className="font-medium text-muted">{key}</dt><dd className="break-all">{value}</dd></div>)}</dl>}
  </div>;
}

export function StatementCompareWorkspace() {
  const queryClient = useQueryClient();
  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents });
  const [result, setResult] = useState<StatementComparisonResult | null>(null);
  const [comparisonId, setComparisonId] = useState<string | null>(null);
  const [resultIds, setResultIds] = useState<[string, string] | null>(null);
  const [reportHref, setReportHref] = useState<string | null>(null);
  const form = useForm<FormData>({ resolver: zodResolver(schema) });
  const compare = useMutation({
    mutationFn: ({ document_id_a, document_id_b }: FormData) => api.compareStatements(document_id_a, document_id_b),
    onSuccess: async (value, selection) => { setResult(value); setComparisonId(value.comparison_id ?? null); setResultIds([selection.document_id_a, selection.document_id_b]); setReportHref(null); await queryClient.invalidateQueries({ queryKey: ["comparisons", "statement"] }); toast.success("Statement comparison saved to your history."); },
    onError: (error) => toast.error(error.message),
  });
  const report = useMutation({
    mutationFn: ([a, b]: [string, string]) => comparisonId ? api.comparisonReport(comparisonId) : api.compareStatementsReport(a, b),
    onSuccess: (blob) => { setReportHref(URL.createObjectURL(blob)); toast.success("Statement comparison report is ready."); },
    onError: (error) => toast.error(error.message),
  });
  useEffect(() => () => { if (reportHref) URL.revokeObjectURL(reportHref); }, [reportHref]);

  const options = (documents.data ?? []).filter((doc) => doc.doc_type.startsWith("bank_statement_") && doc.status === "completed" && (doc.transaction_count ?? 0) > 0);
  return <div className="space-y-6">
    <div><Eyebrow>STATEMENT REVIEW</Eyebrow><h2 className="mt-2 text-xl font-semibold">Compare bank statements</h2><p className="mt-2 max-w-2xl text-xs leading-5 text-muted">Exact matching uses date, signed amount, and normalized description. Differences are review signals, not proof of error or misconduct.</p></div>
    <Surface className="p-5 sm:p-6"><form className="grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-end" onSubmit={form.handleSubmit((values) => compare.mutate(values))}>
      <label className="grid gap-2 text-xs font-medium text-slate-700">Statement A<select className="h-10 rounded-lg border border-line bg-white px-3 text-xs text-ink focus:border-teal" {...form.register("document_id_a")}><option value="">Select a statement</option>{options.map((doc) => <option key={doc.id} value={doc.id}>{doc.filename} · {doc.transaction_count} rows · {doc.currency ?? "currency unknown"}</option>)}</select>{form.formState.errors.document_id_a && <span className="text-[11px] text-red-700">{form.formState.errors.document_id_a.message}</span>}</label>
      <label className="grid gap-2 text-xs font-medium text-slate-700">Statement B<select className="h-10 rounded-lg border border-line bg-white px-3 text-xs text-ink focus:border-teal" {...form.register("document_id_b")}><option value="">Select a different statement</option>{options.map((doc) => <option key={doc.id} value={doc.id}>{doc.filename} · {doc.transaction_count} rows · {doc.currency ?? "currency unknown"}</option>)}</select>{form.formState.errors.document_id_b && <span className="text-[11px] text-red-700">{form.formState.errors.document_id_b.message}</span>}</label>
      <Button type="submit" disabled={compare.isPending || documents.isLoading || options.length < 2}>{compare.isPending ? <LoaderCircle className="animate-spin" size={15} /> : <GitCompareArrows size={15} />}Compare statements</Button>
    </form><p className="mt-4 text-[11px] text-muted">{documents.isLoading ? "Loading your statements…" : options.length < 2 ? "Import at least two supported statements with extracted transactions." : "Only statements owned by your account are listed."}</p>{documents.isError && <p role="alert" className="mt-3 text-xs text-red-700">{documents.error.message}</p>}</Surface>

    <ComparisonHistory kind="statement" onOpen={(saved) => { if (saved.kind !== "statement") return; setResult(saved.result); setComparisonId(saved.id); setResultIds(saved.document_ids); setReportHref(null); form.reset({ document_id_a: saved.document_ids[0], document_id_b: saved.document_ids[1] }); toast.success("Saved statement comparison reopened."); }} />

    {result && <Surface className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-4 sm:px-5"><div><Eyebrow>STATEMENT COMPARISON</Eyebrow><p className="mt-1 text-xs text-muted">{result.a.filename} ↔ {result.b.filename}</p></div><div className="flex items-center gap-2"><Button variant="secondary" size="sm" onClick={() => resultIds && report.mutate(resultIds)} disabled={report.isPending}>{report.isPending ? <LoaderCircle className="animate-spin" size={14} /> : <Download size={14} />}Build PDF report</Button>{reportHref && <a className="rounded-lg border border-teal/20 bg-teal/10 px-3 py-2 text-xs font-semibold text-teal hover:bg-teal/15" href={reportHref} download="bankease-statement-comparison.pdf">Download PDF</a>}</div></div>
      <div className="space-y-5 p-4 sm:p-5">
        <p className={`rounded-lg border p-4 text-xs leading-5 ${result.comparable ? "border-line bg-slate-50 text-muted" : "border-amber-200 bg-amber/5 text-amber"}`}>{result.note}</p>
        {result.comparable && <>
          {result.history_truncated && <p className="rounded-lg border border-amber/20 bg-amber/5 p-3 text-[11px] leading-5 text-amber">This saved result contains a 500-row detail excerpt. Summary counts reflect the full comparison.</p>}
          <section aria-label="Comparison summary" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[["Matched rows", result.summary.matched_transaction_count], ["Only in A", result.summary.only_in_a_count], ["Only in B", result.summary.only_in_b_count], ["Balance differences", result.summary.running_balance_difference_count]].map(([label, value]) => <div key={label} className="rounded-lg border border-line bg-slate-50 p-4"><p className="text-[10px] uppercase tracking-wider text-muted">{label}</p><p className="mt-2 text-lg font-semibold tabular-nums">{value}</p></div>)}
          </section>
          {Object.keys(result.balance_comparison).length > 0 && <section><Eyebrow>STATEMENT BALANCES</Eyebrow><div className="mt-3 grid gap-3 sm:grid-cols-2">{Object.entries(result.balance_comparison).map(([field, values]) => values && <div key={field} className="rounded-lg border border-line p-4"><p className="text-xs font-semibold">{field === "opening_balance_minor" ? "Implied opening balance" : "Closing balance"}</p><p className="mt-2 text-xs text-muted">A: {money(values.a_minor, result.a.currency)} · B: {money(values.b_minor, result.b.currency)}</p><p className="mt-1 text-[11px] text-amber">Difference (B − A): {money(values.difference_minor, result.a.currency)}</p></div>)}</div><p className="mt-2 text-[10px] text-muted">Opening balances are implied from the first transaction row in each statement.</p></section>}
          <section><div className="flex flex-wrap items-end justify-between gap-2"><div><Eyebrow>TRANSACTIONS TO REVIEW</Eyebrow><p className="mt-1 text-[11px] text-muted">{result.summary.difference_count} exact differences; no source transaction is changed or removed.</p></div></div>
            {result.differences.length === 0 && <p className="mt-3 rounded-lg border border-line p-4 text-xs text-muted">No exact transaction or running-balance differences were detected.</p>}
            <div className="mt-3 space-y-3">{result.differences.slice(0, 200).map((difference, index) => <article key={`${difference.kind}-${difference.date}-${difference.a?.row_number ?? "x"}-${difference.b?.row_number ?? "y"}-${index}`} className="rounded-lg border border-line p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><span className="rounded-full bg-amber/10 px-2 py-1 text-[10px] text-amber">{humanize(difference.kind)}</span><p className="mt-3 text-xs font-semibold">{difference.date} · {difference.description}</p></div><p className="text-xs font-semibold tabular-nums">{money(difference.amount_minor, result.a.currency)}</p></div><div className="mt-3 grid gap-2 md:grid-cols-2"><SourceEvidence source={difference.a} label="Statement A" /><SourceEvidence source={difference.b} label="Statement B" /></div></article>)}</div>
            {result.differences.length > 200 && <p className="mt-3 text-[11px] text-muted">Showing the first 200 differences. The PDF report lists up to 100.</p>}
          </section>
        </>}
      </div>
    </Surface>}
  </div>;
}
