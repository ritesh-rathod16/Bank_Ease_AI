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
import type { CompareRow } from "@/types/api";
import { StatementCompareWorkspace } from "@/features/comparison/statement-compare-workspace";
import { FinancialStatementCompareWorkspace } from "@/features/comparison/financial-statement-compare-workspace";
import { ComparisonHistory } from "@/features/comparison/comparison-history";

const fields = ["loan_amount", "interest_rate", "tenure", "emi", "processing_fee"] as const;
const loanDocumentTypes = new Set(["loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure"]);
const overrideSchema = z.object({ loan_amount: z.string().max(80).optional(), interest_rate: z.string().max(80).optional(), tenure: z.string().max(80).optional(), emi: z.string().max(80).optional(), processing_fee: z.string().max(80).optional() }).partial();
const schema = z.object({ document_id_a: z.string().min(1, "Choose Offer A."), document_id_b: z.string().min(1, "Choose Offer B."), overrides_a: overrideSchema.optional(), overrides_b: overrideSchema.optional() }).refine((value) => value.document_id_a !== value.document_id_b, { path: ["document_id_b"], message: "Choose a different document for each offer." });
type FormData = z.infer<typeof schema>;

function nonEmptyOverrides(value: FormData["overrides_a"]) {
  return Object.fromEntries(Object.entries(value ?? {}).filter(([, text]) => Boolean(text?.trim()))) as Record<string, string>;
}

function ComparisonRows({ rows }: { rows: CompareRow[] }) {
  return <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-xs"><thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-muted"><tr><th className="px-4 py-3">Loan detail</th><th className="px-4 py-3">Offer A</th><th className="px-4 py-3">Offer B</th><th className="px-4 py-3">Review status</th></tr></thead><tbody className="divide-y divide-line">{rows.map((row) => <tr className="align-top" key={row.field}><td className="whitespace-nowrap px-4 py-4 font-semibold">{humanize(row.field)}</td>{([row.a, row.b] as const).map((item, index) => <td className="max-w-[310px] whitespace-normal px-4 py-4" key={`${row.field}-${index}`}><span>{item.value ?? "Not found"}</span>{item.manual && <span className="ml-2 rounded border border-amber/20 bg-amber/10 px-1.5 py-0.5 text-[9px] text-amber">{item.manual_source === "saved_correction" ? "Saved correction" : "Comparison override"}</span>}<p className="mt-1 leading-5 text-muted">{item.evidence || "No source passage found."}</p>{item.page && <p className="mt-1 text-[10px] text-teal">Page {item.page}</p>}</td>)}<td className="px-4 py-4"><span className={`rounded-full px-2 py-1 text-[10px] ${row.status === "potential_difference" || row.status === "needs_review" ? "bg-amber/10 text-amber" : row.status === "match" ? "bg-teal/10 text-teal" : "bg-panel-raised text-muted"}`}>{humanize(row.status)}</span></td></tr>)}</tbody></table></div>;
}

export function CompareWorkspace() {
  const [mode, setMode] = useState<"loans" | "statements" | "financial_statements">("loans");
  const queryClient = useQueryClient();
  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents });
  const [rows, setRows] = useState<CompareRow[] | null>(null);
  const [comparisonId, setComparisonId] = useState<string | null>(null);
  const [reportHref, setReportHref] = useState<string | null>(null);
  const form = useForm<FormData>({ resolver: zodResolver(schema) });
  const compare = useMutation({ mutationFn: (values: FormData) => api.compare(values.document_id_a, values.document_id_b, nonEmptyOverrides(values.overrides_a), nonEmptyOverrides(values.overrides_b)), onSuccess: async (result) => { setRows(result.rows); setComparisonId(result.comparison_id); setReportHref(null); await queryClient.invalidateQueries({ queryKey: ["comparisons", "loan"] }); toast.success("Comparison saved to your history."); }, onError: (error) => toast.error(error.message) });
  const report = useMutation({
    mutationFn: (values: FormData) => comparisonId ? api.comparisonReport(comparisonId) : api.compareReport(values.document_id_a, values.document_id_b, nonEmptyOverrides(values.overrides_a), nonEmptyOverrides(values.overrides_b)),
    onSuccess: (blob) => { setReportHref(URL.createObjectURL(blob)); toast.success("Comparison report is ready."); },
    onError: (error) => toast.error(error.message),
  });
  useEffect(() => () => { if (reportHref) URL.revokeObjectURL(reportHref); }, [reportHref]);

  const options = (documents.data ?? []).filter((doc) => loanDocumentTypes.has(doc.doc_type));
  return <div className="space-y-6">
    <div className="flex flex-wrap items-end justify-between gap-4"><div><Eyebrow>DOCUMENT COMPARISON</Eyebrow><h1 className="mt-2 text-3xl font-semibold tracking-[-.04em]">Compare documents</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-muted">Review loan terms, statement transactions, or financial totals with source evidence.</p></div><div aria-label="Comparison type" className="flex flex-wrap rounded-lg border border-line bg-panel p-1"><Button type="button" size="sm" variant={mode === "loans" ? "primary" : "ghost"} aria-pressed={mode === "loans"} onClick={() => setMode("loans")}>Loan offers</Button><Button type="button" size="sm" variant={mode === "statements" ? "primary" : "ghost"} aria-pressed={mode === "statements"} onClick={() => setMode("statements")}>Bank statements</Button><Button type="button" size="sm" variant={mode === "financial_statements" ? "primary" : "ghost"} aria-pressed={mode === "financial_statements"} onClick={() => setMode("financial_statements")}>Financial statements</Button></div></div>
    {mode === "statements" ? <StatementCompareWorkspace /> : mode === "financial_statements" ? <FinancialStatementCompareWorkspace /> : <>
    <Surface className="p-5 sm:p-6"><form className="grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-end" onSubmit={form.handleSubmit((values) => compare.mutate(values))}>
      <label className="grid gap-2 text-xs font-medium text-slate-700">Offer A<select className="h-10 rounded-lg border border-line bg-white px-3 text-xs text-ink focus:border-teal" {...form.register("document_id_a")}><option value="">Select a document</option>{options.map((doc) => <option key={doc.id} value={doc.id}>{doc.filename}</option>)}</select>{form.formState.errors.document_id_a && <span className="text-[11px] text-red-700">{form.formState.errors.document_id_a.message}</span>}</label>
      <label className="grid gap-2 text-xs font-medium text-slate-700">Offer B<select className="h-10 rounded-lg border border-line bg-white px-3 text-xs text-ink focus:border-teal" {...form.register("document_id_b")}><option value="">Select a different document</option>{options.map((doc) => <option key={doc.id} value={doc.id}>{doc.filename}</option>)}</select>{form.formState.errors.document_id_b && <span className="text-[11px] text-red-700">{form.formState.errors.document_id_b.message}</span>}</label>
      <Button type="submit" disabled={compare.isPending || documents.isLoading || options.length < 2}>{compare.isPending ? <LoaderCircle className="animate-spin" size={15} /> : <GitCompareArrows size={15} />}Compare offers</Button>
      <details className="mt-2 border-t border-line pt-4 md:col-span-3"><summary className="cursor-pointer text-xs font-medium text-teal">Enter corrected values for this comparison</summary><p className="mt-2 text-[11px] leading-5 text-muted">Optional values apply only to this comparison and report. They do not change the extracted document or its evidence.</p><div className="mt-4 grid gap-4 lg:grid-cols-2">{(["a", "b"] as const).map((side) => <fieldset className="grid grid-cols-2 gap-3 rounded-lg border border-line bg-slate-50 p-4" key={side}><legend className="px-1 text-xs font-semibold">Offer {side.toUpperCase()}</legend>{fields.map((field) => <label className="grid gap-1.5 text-[10px] text-muted" key={field}>{humanize(field)}<input className="h-9 min-w-0 rounded-md border border-line bg-white px-2 text-xs text-ink outline-none focus:border-teal/70" type="text" inputMode="decimal" placeholder="Use extracted value" maxLength={80} {...form.register(`overrides_${side}.${field}`)} /></label>)}</fieldset>)}</div></details>
    </form><p className="mt-4 text-[11px] text-muted">{documents.isLoading ? "Loading your documents…" : options.length < 2 ? "Add at least two documents before comparing loan offers." : "Both documents must belong to your account."}</p>{documents.isError && <p role="alert" className="mt-3 text-xs text-red-700">{documents.error.message}</p>}</Surface>
    <ComparisonHistory kind="loan" onOpen={(saved) => { if (saved.kind !== "loan") return; setRows(saved.result.rows); setComparisonId(saved.id); setReportHref(null); form.reset({ document_id_a: saved.document_ids[0], document_id_b: saved.document_ids[1], overrides_a: saved.request.overrides_a ?? {}, overrides_b: saved.request.overrides_b ?? {} }); toast.success("Saved comparison reopened."); }} />
    {rows && <Surface className="overflow-hidden"><div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-4 sm:px-5"><div><Eyebrow>COMPARISON RESULT</Eyebrow><p className="mt-1 text-xs text-muted">Source evidence is included where the analyzer found a match.</p></div><div className="flex items-center gap-2"><Button variant="secondary" size="sm" onClick={() => { const values = form.getValues(); const valid = schema.safeParse(values); if (valid.success) report.mutate(valid.data); }} disabled={report.isPending}>{report.isPending ? <LoaderCircle className="animate-spin" size={14} /> : <Download size={14} />}Build PDF report</Button>{reportHref && <a className="rounded-lg border border-teal/20 bg-teal/10 px-3 py-2 text-xs font-semibold text-teal hover:bg-teal/15" href={reportHref} download="bankease-loan-comparison.pdf">Download PDF</a>}</div></div><ComparisonRows rows={rows} /></Surface>}
    <p className="text-[11px] leading-5 text-muted">Comparison uses rule-based extraction. Missing or conflicting values may require checking the original agreements. No lender ranking or financial recommendation is generated.</p>
    </>}
  </div>;
}
