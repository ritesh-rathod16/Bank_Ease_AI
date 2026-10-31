"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, CheckCircle2, CircleAlert, Clock3, FileText, Files, Landmark, Lightbulb, MoreHorizontal, RotateCcw, ShieldCheck, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import { ObligationItems } from "@/features/dashboard/obligation-items";
import { formatCurrency, formatDate, humanize } from "@/lib/utils";
import type { DashboardSummary, DocumentRecord, DocumentSummary, FinanceSummary } from "@/types/api";

const termValue = (doc: DocumentRecord | undefined, key: string) => doc?.terms?.[key]?.effective_value ?? doc?.terms?.[key]?.value ?? null;
const inr = (value: number | null) => value === null ? "Not extracted" : formatCurrency(value);

function MetricCard({ title, value, detail, href, icon: Icon, tone = "blue" }: { title: string; value: string | number; detail: string; href: string; icon: typeof FileText; tone?: "blue" | "amber" | "green" | "violet" }) {
  const tones = { blue: "bg-blue-50 text-blue-700", amber: "bg-amber-50 text-amber-700", green: "bg-emerald-50 text-emerald-700", violet: "bg-violet-50 text-violet-700" };
  return <Link href={href} className="group block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40"><Surface className="h-full p-4 shadow-sm transition group-hover:border-blue-200 group-hover:shadow-md sm:p-5"><div className="flex items-start justify-between gap-3"><span className={`grid size-9 place-items-center rounded-lg ${tones[tone]}`}><Icon size={17} /></span><ArrowRight size={15} className="mt-1 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-teal" /></div><p className="mt-4 text-[11px] font-medium text-muted">{title}</p><p className="mt-1 text-2xl font-semibold tracking-tight text-ink tabular-nums">{value}</p><p className="mt-1 text-[10px] leading-4 text-muted">{detail}</p></Surface></Link>;
}

function Skeleton({ className = "" }: { className?: string }) { return <div className={`animate-pulse rounded-md bg-slate-100 ${className}`} />; }

function dateLabel(value?: string) {
  if (!value) return "Date not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date not recorded" : new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" }).format(date);
}

function RecentDocuments({ documents }: { documents: DocumentSummary[] }) {
  const queryClient = useQueryClient();
  const retry = useMutation({
    mutationFn: api.retryDocument,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      await queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      toast.success("Document sent for reprocessing.");
    },
    onError: (error) => toast.error(error.message),
  });
  const recent = [...documents].sort((a, b) => (b.updated_at ?? b.created_at ?? "").localeCompare(a.updated_at ?? a.created_at ?? "")).slice(0, 6);
  return <Surface className="overflow-hidden shadow-sm">
    <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-4 sm:px-5"><div><h2 className="text-sm font-semibold">Recent documents</h2><p className="mt-1 text-[10px] text-muted">Your latest uploads and analysis status</p></div><Link href="/dashboard/documents" className="inline-flex items-center gap-1 text-[11px] font-semibold text-teal hover:underline">View all <ArrowRight size={13} /></Link></div>
    {recent.length ? <div className="overflow-x-auto"><table className="w-full min-w-[620px] text-left"><thead className="bg-slate-50 text-[9px] font-bold uppercase tracking-wide text-slate-500"><tr><th className="px-4 py-3 sm:px-5">Document</th><th className="px-3 py-3">Type</th><th className="px-3 py-3">Status</th><th className="px-3 py-3">Last updated</th><th className="px-3 py-3 text-right">Actions</th></tr></thead><tbody className="divide-y divide-line">{recent.map((doc) => <tr key={doc.id} className="hover:bg-slate-50"><td className="max-w-[250px] px-4 py-3.5 sm:px-5"><Link href={`/dashboard/documents/${doc.id}`} className="flex items-center gap-2.5 font-medium text-ink hover:text-teal"><span className="grid size-8 shrink-0 place-items-center rounded-lg border border-line bg-white text-blue-700"><FileText size={15} /></span><span className="truncate text-xs">{doc.filename}</span></Link></td><td className="px-3 py-3.5 text-[10px] text-slate-600">{humanize(doc.loan_category || doc.doc_type || "Unknown")}</td><td className="px-3 py-3.5"><span className={`rounded-full px-2 py-1 text-[9px] font-semibold ${doc.status === "completed" ? "bg-emerald-50 text-emerald-700" : doc.status === "needs_review" ? "bg-amber-50 text-amber-800" : doc.status === "failed" ? "bg-red-50 text-red-700" : "bg-slate-100 text-slate-600"}`}>{humanize(doc.status)}</span></td><td className="whitespace-nowrap px-3 py-3.5 text-[10px] text-slate-500">{dateLabel(doc.updated_at ?? doc.created_at)}</td><td className="px-3 py-3.5"><div className="flex items-center justify-end gap-1"><Link href={`/dashboard/documents/${doc.id}`} className="rounded-md px-2 py-1.5 text-[10px] font-semibold text-teal hover:bg-blue-50">Open</Link><details className="group/actions relative"><summary className="grid size-8 cursor-pointer list-none place-items-center rounded-md text-slate-500 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40 [&::-webkit-details-marker]:hidden" aria-label={`More actions for ${doc.filename}`} title="More actions"><MoreHorizontal size={16} /></summary><div className="absolute right-0 top-full z-20 mt-1 w-44 rounded-lg border border-line bg-white p-1.5 text-left shadow-lg"><Link href={`/dashboard/documents/${doc.id}`} className="flex items-center gap-2 rounded-md px-2.5 py-2 text-[11px] text-slate-700 hover:bg-slate-50"><FileText size={13} />Open analysis</Link>{doc.status === "failed" && <button type="button" disabled={retry.isPending} onClick={(event) => { event.currentTarget.closest("details")?.removeAttribute("open"); retry.mutate(doc.id); }} className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[11px] text-slate-700 hover:bg-slate-50 disabled:opacity-50"><RotateCcw size={13} />Retry analysis</button>}</div></details></div></td></tr>)}</tbody></table></div> : <div className="grid justify-items-center px-6 py-9 text-center"><span className="grid size-10 place-items-center rounded-lg bg-blue-50 text-blue-700"><Files size={18} /></span><p className="mt-3 text-xs font-semibold">No documents yet</p><p className="mt-1 text-[10px] text-muted">Upload a PDF, scanned image, or bank statement CSV/XLSX to see your analysis here.</p><Link href="/dashboard/upload" className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-[10px] font-semibold text-white hover:bg-blue-700"><UploadCloud size={13} />Upload a document</Link></div>}
  </Surface>;
}

function ObligationCard() {
  const dashboard = useQuery({ queryKey: ["dashboard-summary"], queryFn: api.dashboard });
  const obligations = dashboard.data?.upcoming_obligations ?? [];
  return <Surface className="h-full p-4 shadow-sm sm:p-5"><div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold">Upcoming obligations</h2><p className="mt-1 text-[10px] text-muted">Future dates confirmed against your documents</p></div><span className="grid size-9 place-items-center rounded-lg bg-blue-50 text-blue-700"><CalendarClock size={17} /></span></div>
    {dashboard.isLoading ? <Skeleton className="mt-5 h-24 w-full" /> : dashboard.isError ? <div className="mt-5 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-800">Could not load confirmed dates. <button className="ml-1 font-semibold underline" onClick={() => void dashboard.refetch()}>Retry</button></div> : obligations.length ? <ObligationItems obligations={obligations.slice(0, 4)} compact /> : <div className="mt-5 rounded-lg border border-dashed border-line bg-slate-50 p-4"><p className="text-xs font-semibold">No confirmed dates yet</p><p className="mt-1 text-[10px] leading-4 text-muted">Dates appear after you confirm an extracted date against its source. No recurring payments are inferred.</p><Link href="/dashboard/documents" className="mt-3 inline-flex items-center gap-1 text-[10px] font-semibold text-teal hover:underline">Review documents <ArrowRight size={12} /></Link></div>}
  </Surface>;
}

function LoanInfo({ document, finance, financeLoading }: { document?: DocumentRecord; finance?: FinanceSummary; financeLoading: boolean }) {
  const values = [
    ["Loan amount", inr(termValue(document, "loan_amount") as number | null)],
    ["Interest rate", termValue(document, "interest_rate") === null ? "Not extracted" : `${termValue(document, "interest_rate")}% ${termValue(document, "interest_type") ? `· ${humanize(String(termValue(document, "interest_type")))}` : ""}`],
    ["Monthly EMI", financeLoading ? "Calculating…" : finance ? formatCurrency(finance.calculated_emi) : inr(termValue(document, "emi") as number | null)],
    ["Tenure", termValue(document, "tenure") === null ? "Not extracted" : `${termValue(document, "tenure")} months`],
    ["First EMI date", formatDate(termValue(document, "first_emi_date") as string | null)],
    ["Maturity date", formatDate(termValue(document, "loan_maturity_date") as string | null)],
  ];
  return <Surface className="p-4 shadow-sm sm:p-5"><div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold">Key home loan information</h2><p className="mt-1 text-[10px] text-muted">Values and calculations from your latest home-loan document</p></div><Landmark size={17} className="text-teal" /></div>{document ? <><p className="mt-3 truncate text-[10px] font-medium text-blue-700">{document.filename}</p><dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3">{values.map(([label, value]) => <div key={label}><dt className="text-[9px] text-muted">{label}</dt><dd className="mt-0.5 text-xs font-semibold tabular-nums">{value}</dd></div>)}</dl>{finance?.note && <p className="mt-3 rounded-md bg-amber-50 p-2 text-[10px] leading-4 text-amber-900">{finance.note}</p>}<Link href={`/dashboard/documents/${document.id}`} className="mt-4 inline-flex items-center gap-1 text-[10px] font-semibold text-teal hover:underline">View source and calculations <ArrowRight size={12} /></Link></> : <div className="mt-5 rounded-lg border border-dashed border-line bg-slate-50 p-4"><p className="text-xs font-semibold">No home loan analyzed</p><p className="mt-1 text-[10px] leading-4 text-muted">Upload a home-loan agreement or key facts statement to see extracted terms here.</p><Link href="/dashboard/upload" className="mt-3 inline-flex items-center gap-1 text-[10px] font-semibold text-teal hover:underline">Analyze a document <ArrowRight size={12} /></Link></div>}</Surface>;
}

function RepaymentCharts({ finance, loading, documentId }: { finance?: FinanceSummary; loading: boolean; documentId?: string }) {
  const schedule = finance?.annual_schedule ?? [];
  const maxBalance = Math.max(...schedule.map((year) => year.ending_balance), 0);
  const maxAnnual = Math.max(...schedule.map((year) => year.principal_paid + year.interest_paid), 0);
  const xAt = (index: number) => schedule.length < 2 ? 210 : 24 + index * (372 / (schedule.length - 1));
  const yAt = (value: number) => 116 - (value / (maxBalance || 1)) * 92;
  const path = schedule.map((year, index) => `${index ? "L" : "M"}${xAt(index)},${yAt(year.ending_balance)}`).join(" ");
  return <>
    <Surface className="p-4 shadow-sm sm:p-5"><div className="flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold">Yearly repayment split</h2><p className="mt-1 text-[10px] text-muted">Principal and interest from the calculated schedule</p></div><span className="flex items-center gap-3 text-[9px] text-muted"><span className="inline-flex items-center gap-1"><i className="size-2 rounded-sm bg-blue-600" />Principal</span><span className="inline-flex items-center gap-1"><i className="size-2 rounded-sm bg-sky-200" />Interest</span></span></div>
      {loading ? <Skeleton className="mt-5 h-36 w-full" /> : schedule.length ? <><div className="mt-5 overflow-x-auto"><svg role="img" aria-label="Year by year principal and interest repayment chart" viewBox="0 0 420 155" className="h-36 min-w-[340px] w-full"><line x1="20" y1="126" x2="408" y2="126" stroke="var(--be-chart-grid)" />{schedule.map((year, index) => { const count = schedule.length; const groupWidth = 380 / count; const barWidth = Math.max(2, Math.min(15, groupWidth * 0.46)); const x = 24 + index * groupWidth + (groupWidth - barWidth) / 2; const principalHeight = ((year.principal_paid / (maxAnnual || 1)) * 98); const interestHeight = ((year.interest_paid / (maxAnnual || 1)) * 98); return <g key={year.year}><title>{`Year ${year.year}: ${formatCurrency(year.principal_paid)} principal; ${formatCurrency(year.interest_paid)} interest`}</title><rect x={x} y={126 - principalHeight} width={barWidth} height={principalHeight} rx="2" fill="var(--be-primary)" /><rect x={x} y={126 - principalHeight - interestHeight} width={barWidth} height={interestHeight} rx="2" fill="var(--be-chart-secondary)" />{(year.year === 1 || year.year === schedule.length || year.year % Math.max(1, Math.ceil(schedule.length / 6)) === 0) && <text x={x + barWidth / 2} y="145" textAnchor="middle" fill="var(--be-chart-text)" fontSize="8">Y{year.year}</text>}</g>;})}</svg></div><p className="mt-1 text-[9px] text-muted">Hover over a bar for its INR breakdown.</p></> : <div className="mt-5 grid h-36 place-items-center rounded-lg border border-dashed border-line bg-slate-50 px-4 text-center text-[10px] leading-4 text-muted">{documentId ? "A complete principal, rate, and tenure are needed for this calculation." : "Analyze a home-loan document to see a validated repayment breakdown."}</div>}</Surface>
    <Surface className="p-4 shadow-sm sm:p-5"><div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold">Outstanding principal</h2><p className="mt-1 text-[10px] text-muted">Estimated balance after each year under a fixed-rate schedule</p></div><span className="text-[9px] font-medium text-blue-700">INR</span></div>{loading ? <Skeleton className="mt-5 h-36 w-full" /> : schedule.length ? <><div className="mt-5 overflow-x-auto"><svg role="img" aria-label="Yearly remaining principal chart" viewBox="0 0 420 150" className="h-36 min-w-[340px] w-full"><line x1="20" y1="126" x2="408" y2="126" stroke="var(--be-chart-grid)" /><path d={`${path} L${xAt(schedule.length - 1)},126 L${xAt(0)},126 Z`} fill="var(--be-chart-fill)" /><path d={path} fill="none" stroke="var(--be-primary)" strokeWidth="2.5" />{schedule.map((year, index) => <g key={year.year} tabIndex={0}><title>{`End of year ${year.year}: ${formatCurrency(year.ending_balance)} remaining`}</title><circle cx={xAt(index)} cy={yAt(year.ending_balance)} r="3" fill="var(--be-primary)" /></g>)}<text x="22" y="145" fill="var(--be-chart-text)" fontSize="8">Year 1</text><text x="408" y="145" textAnchor="end" fill="var(--be-chart-text)" fontSize="8">Year {schedule.length}</text></svg></div><p className="mt-1 text-[9px] text-muted">Hover over a point for the estimated year-end balance.</p></> : <div className="mt-5 grid h-36 place-items-center rounded-lg border border-dashed border-line bg-slate-50 px-4 text-center text-[10px] leading-4 text-muted">A supported loan schedule will appear after principal, rate, and tenure are available.</div>}</Surface>
  </>;
}

function Completeness({ document, documents }: { document?: DocumentRecord; documents: DocumentSummary[] }) {
  const termKeys = ["loan_amount", "interest_rate", "tenure", "emi"];
  const extracted = termKeys.filter((key) => termValue(document, key) !== null);
  const collateral = document?.clauses?.some((clause) => clause.category === "Collateral and security") ?? false;
  const schedule = documents.some((item) => item.doc_type === "repayment_schedule");
  const annexure = documents.some((item) => item.doc_type === "annexure");
  const feeSchedule = documents.some((item) => item.doc_type === "fee_schedule");
  const items = [
    ["Loan agreement", documents.some((item) => item.doc_type === "loan_agreement") ? "Detected" : "Not found in analyzed documents", documents.some((item) => item.doc_type === "loan_agreement")],
    ["Interest terms", termValue(document, "interest_rate") !== null ? "Extracted" : "Not extracted", termValue(document, "interest_rate") !== null],
    ["Repayment schedule", schedule ? "Separate document found" : "Not found", schedule],
    ["Fee schedule", feeSchedule ? "Separate document found" : "Not found", feeSchedule],
    ["Collateral reference", collateral ? "Mention detected" : "Not detected", collateral],
    ["Annexures", annexure ? "Document found" : "Not found", annexure],
    ["Signatures", "Not checked by current extractor", false],
    ["Terms and conditions", (document?.clauses?.length ?? 0) > 0 ? "Rule-matched passages found" : "Not detected", (document?.clauses?.length ?? 0) > 0],
  ] as const;
  return <Surface className="p-4 shadow-sm sm:p-5"><div className="flex items-start justify-between gap-3"><div><h2 className="text-sm font-semibold">Document coverage</h2><p className="mt-1 text-[10px] text-muted">A clear view of what the extractor found</p></div><span className="rounded-lg bg-blue-50 px-2.5 py-1.5 text-xs font-bold text-blue-700">{extracted.length}/{termKeys.length} terms</span></div><div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-blue-600 transition-all" style={{ width: `${(extracted.length / termKeys.length) * 100}%` }} /></div><p className="mt-1 text-[9px] text-muted">Coverage is the share of four supported loan values located. It does not measure legal completeness or contract validity.</p><ul className="mt-4 grid gap-2 sm:grid-cols-2">{items.map(([label, status, found]) => <li key={label} className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-2"><span className={found ? "text-emerald-600" : label === "Signatures" ? "text-slate-400" : "text-amber-600"}>{found ? <CheckCircle2 size={14} /> : label === "Signatures" ? <ShieldCheck size={14} /> : <CircleAlert size={14} />}</span><span className="min-w-0"><span className="block text-[10px] font-semibold">{label}</span><span className="block text-[9px] text-muted">{status}</span></span></li>)}</ul></Surface>;
}

export function DashboardHome() {
  const dashboard = useQuery({ queryKey: ["dashboard-summary"], queryFn: api.dashboard, staleTime: 20_000 });
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 60_000 });
  const greeting = "Good morning";
  const summary: DashboardSummary | undefined = dashboard.data;
  const rows = summary?.documents ?? [];
  const homeLoanQuery = useQuery({ queryKey: ["document", summary?.home_loan_document_id], queryFn: () => api.document(summary!.home_loan_document_id!), enabled: Boolean(summary?.home_loan_document_id), staleTime: 60_000, retry: false });
  const latestHomeLoan = homeLoanQuery.data;
  const principal = termValue(latestHomeLoan, "loan_amount");
  const rate = termValue(latestHomeLoan, "interest_rate");
  const months = termValue(latestHomeLoan, "tenure");
  const hasCalcInputs = typeof principal === "number" && principal > 0 && typeof rate === "number" && rate >= 0 && typeof months === "number" && months > 0;
  const finance = useQuery({ queryKey: ["dashboard-finance", latestHomeLoan?.id, principal, rate, months], queryFn: () => api.calculate(latestHomeLoan!.id), enabled: Boolean(latestHomeLoan && hasCalcInputs), staleTime: 60_000, retry: false });
  const categories = Object.entries(summary?.findings_by_category ?? {});
  const isLoading = dashboard.isLoading;

  return <div className="space-y-5">
    <section className="flex flex-wrap items-end justify-between gap-4 rounded-xl border border-line bg-white p-5 shadow-sm sm:p-6"><div><Eyebrow>YOUR FINANCIAL DOCUMENTS</Eyebrow><h1 className="mt-2 text-2xl font-semibold tracking-tight sm:text-[28px]">{greeting}, {me.data?.name.split(" ")[0] ?? "there"}!</h1><p className="mt-1 text-sm text-slate-600">Your financial documents, simplified and organized.</p></div><div className="flex flex-wrap gap-2"><Link href="/dashboard/upload" className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-blue-600 px-4 text-xs font-semibold text-white shadow-sm hover:bg-blue-700"><UploadCloud size={15} />Analyze a document</Link><Link href="/dashboard/documents" className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-line bg-white px-4 text-xs font-semibold text-slate-700 hover:bg-slate-50"><Files size={15} />Upload documents</Link></div></section>
    {dashboard.isError && <Surface className="flex flex-wrap items-center justify-between gap-3 border-red-200 p-4"><p role="alert" className="text-xs text-red-700">Could not load your dashboard data: {dashboard.error.message}</p><Button size="sm" variant="secondary" onClick={() => void dashboard.refetch()}>Try again</Button></Surface>}
    <section aria-label="Account summary" className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
      {isLoading ? Array.from({ length: 4 }, (_, index) => <Surface key={index} className="p-5"><Skeleton className="h-9 w-9" /><Skeleton className="mt-4 h-3 w-28" /><Skeleton className="mt-2 h-7 w-16" /><Skeleton className="mt-2 h-3 w-36" /></Surface>) : <>
        <MetricCard title="Documents analyzed" value={summary?.documents_analyzed ?? "—"} detail={summary ? "Completed analyses in your account" : "Summary is unavailable"} href="/dashboard/documents" icon={FileText} tone="blue" />
        <MetricCard title="Items need review" value={summary?.review_count ?? "—"} detail={summary ? "Open tasks, flagged fields and statement signals" : "Summary is unavailable"} href="/dashboard/reviews" icon={CircleAlert} tone="amber" />
        <MetricCard title="Upcoming obligations" value={summary?.upcoming_obligations.length ?? "—"} detail={summary?.upcoming_obligations[0] ? `Next: ${formatDate(summary.upcoming_obligations[0].due_date)}` : summary ? "Only owner-confirmed dates are shown" : "Summary is unavailable"} href="/dashboard/tools/obligations" icon={CalendarClock} tone="green" />
        <MetricCard title="Insights & findings" value={summary?.finding_count ?? "—"} detail={summary ? "Rule-matched passages across analyzed documents" : "Summary is unavailable"} href="/dashboard/search" icon={Lightbulb} tone="violet" />
      </>}
    </section>
    <section className="grid items-stretch gap-4 2xl:grid-cols-[minmax(0,1.65fr)_minmax(280px,1fr)]">
      <div className="min-w-0">{dashboard.isLoading ? <Surface className="p-5"><Skeleton className="h-8 w-full" /><Skeleton className="mt-4 h-40 w-full" /></Surface> : dashboard.isSuccess && <RecentDocuments documents={rows} />}</div>
      <ObligationCard />
    </section>
    <section><div className="mb-3 flex items-center gap-2"><span className="grid size-7 place-items-center rounded-lg bg-blue-50 text-blue-700"><Landmark size={15} /></span><div><h2 className="text-sm font-semibold">Financial overview</h2><p className="text-[10px] text-muted">Calculated only from supported values in your analyzed documents</p></div></div><div className="grid gap-4 2xl:grid-cols-3"><LoanInfo document={latestHomeLoan} finance={finance.data} financeLoading={finance.isFetching} /><RepaymentCharts finance={finance.data} loading={finance.isFetching} documentId={latestHomeLoan?.id} /></div></section>
    <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <Completeness document={latestHomeLoan} documents={rows} />
      <Surface className="p-4 shadow-sm sm:p-5">
        <div className="flex items-start justify-between"><div><h2 className="text-sm font-semibold">Findings by category</h2><p className="mt-1 text-[10px] text-muted">Rule-matched passages with document evidence</p></div><Lightbulb size={17} className="text-teal" /></div>
        {categories.length ? <ul className="mt-4 space-y-3">{categories.sort((a, b) => b[1] - a[1]).slice(0, 7).map(([category, count]) => <li key={category}><Link href={`/dashboard/search?q=${encodeURIComponent(category)}`} className="group block"><div className="flex items-center justify-between gap-3 text-[10px]"><span className="font-medium group-hover:text-teal">{category}</span><span className="tabular-nums text-muted">{count}</span></div><div className="mt-1.5 h-1.5 rounded-full bg-slate-100"><div className="h-full rounded-full bg-blue-500" style={{ width: `${(count / Math.max(...categories.map((entry) => entry[1]))) * 100}%` }} /></div></Link></li>)}</ul> : <div className="mt-4 rounded-lg border border-dashed border-line bg-slate-50 p-4"><p className="text-xs font-semibold">No findings yet</p><p className="mt-1 text-[10px] leading-4 text-muted">Rule-matched clauses from your supported analyzed documents will appear here with links to their source evidence.</p><Link href="/dashboard/upload" className="mt-3 inline-flex items-center gap-1 text-[10px] font-semibold text-teal">Upload a document <ArrowRight size={12} /></Link></div>}
        <p className="mt-4 flex items-center gap-1 text-[9px] text-slate-500"><ShieldCheck size={11} />A finding is a review prompt, not proof of an issue.</p>
      </Surface>
    </section>
    <p className="flex items-center gap-1.5 text-[9px] text-slate-500"><Clock3 size={11} />Dashboard values refresh from your account&apos;s saved document analyses. No historical period comparison is available yet.</p>
  </div>;
}
