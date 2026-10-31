"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, CheckCircle2, Copy, FileText, ListChecks, LoaderCircle, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import { ObligationItems } from "@/features/dashboard/obligation-items";
import { humanize } from "@/lib/utils";

const glossary = [
  ["EMI", "Equated monthly installment: the regular amount scheduled for a loan payment. The agreement may define how the lender applies each payment."],
  ["Reducing-balance interest", "Interest calculated on the principal that remains outstanding. The dashboard calculation uses this method when principal, rate, and tenure are available."],
  ["Fixed interest rate", "A rate described as fixed in the source document. Confirm any conditions, reset clauses, and the period for which the rate applies."],
  ["Floating interest rate", "A rate that may change under terms such as a benchmark and spread. Check the document for reset timing and how changes affect the installment or tenure."],
  ["Processing fee", "A charge associated with processing a loan application. Check whether tax or other charges are listed separately."],
  ["Prepayment", "Paying some or all of the outstanding loan before its scheduled end. Review the agreement for eligibility, timing, and any stated charges."],
  ["Tenure", "The stated length of the loan, often expressed in months or years. Confirm it against the repayment schedule."],
];

function CalendarAndObligations({ calendar }: { calendar: boolean }) {
  const dashboard = useQuery({ queryKey: ["dashboard-summary"], queryFn: api.dashboard });
  const obligations = dashboard.data?.upcoming_obligations ?? [];
  return <div className="space-y-5"><section><Eyebrow>FINANCIAL PLANNING</Eyebrow><h1 className="mt-2 text-2xl font-semibold tracking-tight">{calendar ? "Financial calendar" : "My obligations"}</h1><p className="mt-1 max-w-2xl text-sm leading-6 text-muted">Dates appear here only after you confirm an extracted date against its source page. Recurring payments are not inferred.</p></section><Surface className="p-5 shadow-sm sm:p-6"><div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-lg bg-blue-50 text-blue-700">{calendar ? <CalendarDays size={18} /> : <ListChecks size={18} />}</span><div><h2 className="text-sm font-semibold">{obligations.length ? `${obligations.length} confirmed future date${obligations.length === 1 ? "" : "s"}` : "Confirmed dates from your documents"}</h2><p className="mt-1 text-xs text-muted">{dashboard.data?.obligation_note ?? "Only dates confirmed by the document owner are included."}</p></div></div>{dashboard.isLoading && <p className="mt-5 flex items-center gap-2 text-xs text-muted"><LoaderCircle size={14} className="animate-spin" />Loading your confirmed dates...</p>}{dashboard.isError && <div className="mt-5 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-800"><p role="alert">Could not load your financial dates: {dashboard.error.message}</p><button className="mt-2 font-semibold underline" onClick={() => void dashboard.refetch()}>Try again</button></div>}{!dashboard.isLoading && !dashboard.isError && obligations.length > 0 && <ObligationItems obligations={obligations} />}{!dashboard.isLoading && !dashboard.isError && obligations.length === 0 && <div className="mt-5 rounded-lg border border-dashed border-line bg-slate-50 p-4"><p className="text-xs font-semibold">No confirmed upcoming dates</p><p className="mt-1 max-w-2xl text-[11px] leading-5 text-muted">Open a supported loan document, review its extracted date fields and save a confirmation only after checking the original page. Past dates and recurring installments are not shown as upcoming events.</p><Link href="/dashboard/documents" className="mt-3 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700"><FileText size={14} />Review your documents</Link></div>}</Surface></div>;
}

function MeetingPreparation() {
  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents });
  const loans = (documents.data ?? []).filter((doc) => !doc.doc_type.startsWith("bank_statement_") && doc.page_count > 0);
  const [selectedId, setSelectedId] = useState("");
  const selected = selectedId || loans[0]?.id || "";
  const detail = useQuery({ queryKey: ["document", selected], queryFn: () => api.document(selected), enabled: Boolean(selected), staleTime: 60_000 });
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const doc = detail.data;
  const questions = useMemo(() => {
    if (!doc) return [];
    const items: Array<{ id: string; text: string; evidence?: string; page?: number }> = [];
    for (const key of ["loan_amount", "interest_rate", "tenure", "emi", "processing_fee", "late_fee"]) {
      const term = doc.terms[key];
      if (!term || term.value === null || ["review", "ambiguous", "conflict"].includes(term.status)) {
        items.push({ id: `field-${key}`, text: `Please confirm the ${humanize(key).toLowerCase()} and point me to the governing page.`, evidence: term?.evidence ?? undefined, page: term?.page ?? undefined });
      }
    }
    for (const clause of (doc.clauses ?? []).slice(0, 8)) {
      items.push({ id: `clause-${clause.page}-${clause.category}-${items.length}`, text: `What does the ${clause.category.toLowerCase()} clause mean in practice?`, evidence: clause.evidence, page: clause.page });
    }
    if (!items.length) items.push({ id: "review-schedule", text: "Can we review the repayment schedule against the loan amount, rate, and tenure?" });
    return items;
  }, [doc]);
  const checkedCount = questions.filter((item) => checked[item.id]).length;

  async function copyChecklist() {
    const lines = [`Bank meeting preparation — ${doc?.filename ?? "Selected document"}`, "", ...questions.map((item) => `${checked[item.id] ? "[x]" : "[ ]"} ${item.text}${item.page ? ` (Page ${item.page})` : ""}`)];
    await navigator.clipboard.writeText(lines.join("\n"));
  }

  return <div className="space-y-5"><section><Eyebrow>PREPARE WITH SOURCE EVIDENCE</Eyebrow><h1 className="mt-2 text-2xl font-semibold tracking-tight">Meeting preparation</h1><p className="mt-1 max-w-2xl text-sm leading-6 text-muted">Build a discussion list from fields that need review and clauses extracted from one of your loan documents.</p></section><Surface className="p-4 shadow-sm"><label className="grid max-w-xl gap-1.5 text-[10px] font-semibold text-slate-600">Choose a loan PDF<select className="h-10 rounded-lg border border-line bg-white px-3 text-xs text-ink outline-none focus:border-teal" value={selected} onChange={(event) => { setSelectedId(event.target.value); setChecked({}); }}><option value="">Choose a document</option>{loans.map((loan) => <option key={loan.id} value={loan.id}>{loan.filename}</option>)}</select></label>{documents.isLoading && <p className="mt-3 text-xs text-muted">Loading your documents…</p>}{documents.isError && <p role="alert" className="mt-3 text-xs text-red-700">{documents.error.message}</p>}{!documents.isLoading && loans.length === 0 && <p className="mt-3 text-xs leading-5 text-muted">No supported loan PDFs are available. Upload a loan agreement or key facts statement first.</p>}</Surface>
    {doc && <Surface className="p-4 shadow-sm sm:p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm font-semibold">Questions based on extracted evidence</h2><p className="mt-1 text-[10px] text-muted">{checkedCount} of {questions.length} marked for your meeting · {doc.filename}</p></div><Button size="sm" variant="secondary" onClick={() => void copyChecklist()}><Copy size={13} />Copy checklist</Button></div>{detail.isLoading && <p className="mt-4 text-xs text-muted">Loading extracted fields…</p>}{detail.isError && <p role="alert" className="mt-4 text-xs text-red-700">{detail.error.message}</p>}<ul className="mt-4 divide-y divide-line">{questions.map((item) => <li key={item.id} className="flex items-start gap-3 py-3"><input type="checkbox" checked={Boolean(checked[item.id])} onChange={(event) => setChecked({ ...checked, [item.id]: event.target.checked })} className="mt-0.5 size-4 accent-blue-600" aria-label={`Mark complete: ${item.text}`} /><div className="min-w-0"><p className="text-xs font-medium">{item.text}</p>{(item.page || item.evidence) && <p className="mt-1 text-[10px] leading-4 text-muted">{item.page ? `Page ${item.page} · ` : ""}{item.evidence}</p>}</div></li>)}</ul><p className="mt-3 flex items-center gap-1.5 text-[9px] text-slate-500"><ShieldAlert size={11} />Questions are preparation prompts, not legal advice or evidence of a defect.</p></Surface>}
  </div>;
}

function Glossary() {
  return <div className="space-y-5"><section><Eyebrow>PLAIN LANGUAGE</Eyebrow><h1 className="mt-2 text-2xl font-semibold tracking-tight">Financial glossary</h1><p className="mt-1 text-sm text-muted">Short explanations of terms you may see in a loan agreement.</p></section><div className="grid gap-3 md:grid-cols-2">{glossary.map(([term, meaning]) => <Surface key={term} className="p-4 shadow-sm"><h2 className="text-sm font-semibold">{term}</h2><p className="mt-2 text-xs leading-5 text-slate-600">{meaning}</p></Surface>)}</div><p className="text-[10px] text-muted">Meanings depend on the wording of a particular document. Check the contract definitions and ask the lender when a term is unclear.</p></div>;
}

function Guides() {
  const guides = [
    ["Before signing a loan agreement", ["Compare the sanctioned amount, interest rate, tenure, and EMI with the key facts statement.", "Check whether the rate is fixed or floating and what can change it.", "Review processing fees, late charges, insurance, and prepayment conditions.", "Locate the repayment schedule and check that dates and amounts are clear.", "Keep a copy of every annexure and signed version."]],
    ["Before a bank review meeting", ["Bring the original agreement and any revised copy.", "Write down the page number beside each question.", "Ask how a rate reset affects the EMI or tenure.", "Ask for an explanation of each fee and tax.", "Request written confirmation for any answer that changes a financial term."]],
    ["When checking a bank statement", ["Confirm the statement period and account identity before sharing it.", "Check opening and closing balances against the source.", "Review duplicate candidates against receipts; similar entries can be legitimate.", "Keep source rows intact and record corrections separately.", "Use statement comparisons as review signals, not proof of wrongdoing."]],
  ] as const;
  return <div className="space-y-5"><section><Eyebrow>HELPFUL CHECKLISTS</Eyebrow><h1 className="mt-2 text-2xl font-semibold tracking-tight">Guides & checklists</h1><p className="mt-1 max-w-2xl text-sm leading-6 text-muted">Practical steps for reviewing supported banking documents. These checklists do not replace advice from your lender or a qualified professional.</p></section><div className="grid gap-4 xl:grid-cols-2">{guides.map(([title, steps]) => <Surface key={title} className="p-5 shadow-sm"><div className="flex items-center gap-2"><CheckCircle2 size={17} className="text-teal" /><h2 className="text-sm font-semibold">{title}</h2></div><ol className="mt-4 space-y-3">{steps.map((step, index) => <li key={step} className="flex gap-3 text-xs leading-5 text-slate-700"><span className="grid size-5 shrink-0 place-items-center rounded-full bg-blue-50 text-[10px] font-semibold text-blue-700">{index + 1}</span>{step}</li>)}</ol></Surface>)}</div></div>;
}

export function DashboardTool({ tool }: { tool: string }) {
  if (tool === "calendar") return <CalendarAndObligations calendar />;
  if (tool === "obligations") return <CalendarAndObligations calendar={false} />;
  if (tool === "meeting-prep") return <MeetingPreparation />;
  if (tool === "glossary") return <Glossary />;
  return <Guides />;
}
