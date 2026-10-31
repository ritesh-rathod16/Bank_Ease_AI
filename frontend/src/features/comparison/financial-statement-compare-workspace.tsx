"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, GitCompareArrows, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button } from "@/components/ui/button";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { ComparisonHistory } from "@/features/comparison/comparison-history";
import { api } from "@/lib/api/queries";
import { humanize } from "@/lib/utils";
import type { FinancialStatementComparisonResult, SavedComparison } from "@/types/api";

const schema = z.object({ document_id_a: z.string().min(1, "Choose document A."), document_id_b: z.string().min(1, "Choose document B.") })
  .refine((values) => values.document_id_a !== values.document_id_b, { path: ["document_id_b"], message: "Choose a different document." });
type FormData = z.infer<typeof schema>;

const badgeClass: Record<string, string> = {
  match: "bg-emerald-50 text-emerald-800",
  potential_difference: "bg-amber-50 text-amber-900",
  needs_review: "bg-amber-50 text-amber-900",
  not_comparable: "bg-slate-100 text-slate-700",
  not_found: "bg-slate-100 text-slate-600",
};

export function FinancialStatementCompareWorkspace() {
  const queryClient = useQueryClient();
  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents });
  const [result, setResult] = useState<FinancialStatementComparisonResult | null>(null);
  const [comparisonId, setComparisonId] = useState<string | null>(null);
  const [reportHref, setReportHref] = useState<string | null>(null);
  const form = useForm<FormData>({ resolver: zodResolver(schema) });
  const compare = useMutation({
    mutationFn: (values: FormData) => api.compareFinancialStatements(values.document_id_a, values.document_id_b),
    onSuccess: async (value) => {
      setResult(value);
      setComparisonId(value.comparison_id ?? null);
      setReportHref(null);
      await queryClient.invalidateQueries({ queryKey: ["comparisons", "financial_statement"] });
      toast.success("Financial-statement comparison saved.");
    },
    onError: (error) => toast.error(error.message),
  });
  const report = useMutation({
    mutationFn: (id: string) => api.comparisonReport(id),
    onSuccess: (blob) => { setReportHref(URL.createObjectURL(blob)); toast.success("Comparison report is ready."); },
    onError: (error) => toast.error(error.message),
  });
  useEffect(() => () => { if (reportHref) URL.revokeObjectURL(reportHref); }, [reportHref]);

  const options = (documents.data ?? []).filter((doc) => doc.doc_type === "financial_statement");
  const openSaved = (saved: SavedComparison) => {
    if (saved.kind !== "financial_statement") return;
    setResult(saved.result);
    setComparisonId(saved.id);
    setReportHref(null);
    form.reset({ document_id_a: saved.document_ids[0], document_id_b: saved.document_ids[1] });
    toast.success("Saved comparison reopened.");
  };

  return <div className="space-y-5">
    <Surface className="p-5 sm:p-6">
      <Eyebrow>FINANCIAL STATEMENT COMPARISON</Eyebrow>
      <p className="mt-2 max-w-3xl text-xs leading-5 text-muted">Compare labeled totals with their source pages. BankEase calculates a difference only when both cited rows explicitly show the same currency and scale; it does not infer reporting dates or table-header units.</p>
      <form className="mt-4 grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-end" onSubmit={form.handleSubmit((values) => compare.mutate(values))}>
        {(["a", "b"] as const).map((side) => <label className="grid gap-2 text-xs font-medium text-slate-700" key={side}>Document {side.toUpperCase()}<select className="h-10 rounded-lg border border-line bg-white px-3 text-xs text-ink focus:border-teal" {...form.register(`document_id_${side}`)}><option value="">Select a financial statement</option>{options.map((doc) => <option key={doc.id} value={doc.id}>{doc.filename}</option>)}</select>{form.formState.errors[`document_id_${side}`] && <span className="text-[11px] text-red-700">{form.formState.errors[`document_id_${side}`]?.message}</span>}</label>)}
        <Button type="submit" disabled={compare.isPending || documents.isLoading || options.length < 2}>{compare.isPending ? <LoaderCircle className="animate-spin" size={15} /> : <GitCompareArrows size={15} />}Compare statements</Button>
      </form>
      <p className="mt-3 text-[11px] text-muted">{documents.isLoading ? "Loading your documents…" : options.length < 2 ? "Upload two supported financial-statement PDFs to compare them." : "Only documents in your account are listed."}</p>
      {documents.isError && <p role="alert" className="mt-3 text-xs text-red-700">{documents.error.message}</p>}
    </Surface>

    <ComparisonHistory kind="financial_statement" onOpen={openSaved} />

    {result && <Surface className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-4 sm:px-5"><div><Eyebrow>COMPARISON RESULT</Eyebrow><div className="mt-2 grid gap-1 text-xs text-muted sm:grid-cols-2 sm:gap-x-5"><p>{result.a.filename} · {result.a.reporting_period.value ?? (result.a.reporting_period.status === "ambiguous" ? "Period needs review" : "Period not found")}</p><p>{result.b.filename} · {result.b.reporting_period.value ?? (result.b.reporting_period.status === "ambiguous" ? "Period needs review" : "Period not found")}</p></div></div><div className="flex items-center gap-2"><Button variant="secondary" size="sm" onClick={() => comparisonId && report.mutate(comparisonId)} disabled={!comparisonId || report.isPending}>{report.isPending ? <LoaderCircle className="animate-spin" size={14} /> : <Download size={14} />}Build PDF report</Button>{reportHref && <a className="rounded-lg border border-teal/20 bg-teal/10 px-3 py-2 text-xs font-semibold text-teal hover:bg-teal/15" href={reportHref} download="bankease-financial-statement-comparison.pdf">Download PDF</a>}</div></div>
      <div className="space-y-4 p-4 sm:p-5">
        <p className="rounded-lg border border-line bg-slate-50 p-4 text-xs leading-5 text-muted">{result.note}</p>
        <section aria-label="Comparison summary" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{([["Comparable fields", result.summary.comparable_field_count], ["Potential differences", result.summary.difference_count], ["Need review", result.summary.needs_review_count], ["No verified common unit", result.summary.not_comparable_count]] as const).map(([label, value]) => <div className="rounded-lg border border-line bg-slate-50 p-4" key={label}><p className="text-[10px] uppercase tracking-wider text-muted">{label}</p><p className="mt-2 text-lg font-semibold tabular-nums">{value}</p></div>)}</section>
        <div className="overflow-x-auto"><table className="w-full min-w-[850px] text-left text-xs"><thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-muted"><tr><th className="px-3 py-3">Labeled total</th><th className="px-3 py-3">Document A</th><th className="px-3 py-3">Document B</th><th className="px-3 py-3">B − A</th><th className="px-3 py-3">Status</th></tr></thead><tbody className="divide-y divide-line">{result.rows.map((row) => <tr className="align-top" key={row.field}><th className="whitespace-nowrap px-3 py-4 font-semibold">{row.label}</th>{([row.a, row.b] as const).map((value, index) => <td className="max-w-[330px] whitespace-normal px-3 py-4" key={`${row.field}-${index}`}><p className="font-medium">{value.value ?? "Not found"}{value.manual && <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[9px] text-amber-900">Saved correction</span>}</p><p className="mt-1 text-[10px] leading-4 text-muted">{value.evidence ?? "No source passage extracted."}</p>{value.page && <p className="mt-1 text-[10px] text-teal">Page {value.page}</p>}</td>)}<td className="whitespace-nowrap px-3 py-4 tabular-nums">{row.difference_b_minus_a === null ? "—" : `${row.difference_b_minus_a} ${row.unit?.currency} (${humanize(row.unit?.scale ?? "")})`}</td><td className="px-3 py-4"><span className={`rounded-full px-2 py-1 text-[10px] ${badgeClass[row.status] ?? "bg-slate-100 text-slate-700"}`}>{humanize(row.status)}</span></td></tr>)}</tbody></table></div>
        <p className="text-[10px] leading-5 text-muted">Differences are review signals. The app does not determine whether a difference is an accounting error or misconduct. Confirm periods, units, and figures in the original reports.</p>
      </div>
    </Surface>}
  </div>;
}
