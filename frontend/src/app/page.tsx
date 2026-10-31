import Link from "next/link";
import { ArrowDownRight, ArrowRight, FileSearch2, ShieldCheck, WalletCards } from "lucide-react";
import { Eyebrow, Surface } from "@/components/ui/surface";

const steps = [
  { icon: FileSearch2, number: "01", title: "Read the agreement", text: "Extract key terms from selectable PDF text and keep page evidence beside each value." },
  { icon: WalletCards, number: "02", title: "Check the repayment", text: "Compare stated installments with a reducing-balance EMI calculation and its assumptions." },
  { icon: ShieldCheck, number: "03", title: "Review with context", text: "Ask questions that return matching source passages. Missing details stay clearly marked." },
];

export default function HomePage() {
  return <main className="min-h-screen overflow-hidden">
    <header className="mx-auto flex w-full max-w-7xl items-center justify-between px-5 py-5 sm:px-8">
      <Link href="/" className="flex items-center gap-3 text-sm font-semibold"><span className="grid size-9 place-items-center rounded-xl border border-teal/25 bg-teal/10 text-teal"><ShieldCheck size={19} /></span>BankEase <span className="-ml-2 text-teal">AI</span></Link>
      <nav className="flex items-center gap-3"><Link className="px-3 py-2 text-xs text-muted hover:text-ink" href="/login">Sign in</Link><Link className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-[#10201c] hover:bg-[#87d4c5]" href="/register">Create account <ArrowRight size={15} /></Link></nav>
    </header>
    <section className="relative mx-auto grid min-h-[610px] w-full max-w-7xl items-center gap-10 px-5 py-16 sm:px-8 lg:grid-cols-[1.1fr_.9fr] lg:py-24">
      <div className="pointer-events-none absolute -right-56 top-10 size-[520px] rounded-full border border-teal/10" />
      <div className="relative z-10 max-w-2xl"><Eyebrow>LOAN DOCUMENT CLARITY</Eyebrow><h1 className="mt-5 text-5xl font-semibold leading-[1.05] tracking-[-.055em] sm:text-6xl">Know what your loan agreement <span className="text-teal">actually says.</span></h1><p className="mt-6 max-w-xl text-base leading-7 text-muted">Review key terms, repayment math, and clauses with the source passages close at hand. Built to help you read the document, not make the decision for you.</p><div className="mt-8 flex flex-wrap gap-3"><Link className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-[#10201c] hover:bg-[#87d4c5]" href="/register">Create a private workspace <ArrowRight size={16} /></Link><Link className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-line bg-panel px-4 text-sm font-medium text-ink hover:bg-panel-raised" href="#how-it-works">See how it works <ArrowDownRight size={15} /></Link></div><p className="mt-5 text-xs text-muted">Selectable text PDFs · Source page evidence · No model-generated figures</p></div>
      <Surface className="relative z-10 overflow-hidden p-5 sm:p-7">
        <div className="flex items-start justify-between border-b border-line pb-5"><div><Eyebrow>DOCUMENT REVIEW</Eyebrow><p className="mt-2 text-sm font-semibold">A clearer view of the fine print</p></div><span className="rounded-full border border-teal/20 bg-teal/10 px-2.5 py-1 text-[10px] font-semibold text-teal">EVIDENCE FIRST</span></div>
        <div className="mt-5 space-y-3">
          {[{ title: "Loan amount", value: "Source-linked value", page: "Page reference" }, { title: "Interest rate", value: "Review the rate type", page: "Evidence passage" }, { title: "Monthly installment", value: "Check against EMI math", page: "Calculation assumptions" }].map((item) => <div key={item.title} className="grid grid-cols-[1fr_auto] items-center gap-3 rounded-lg border border-line bg-[#131c19] p-3"><div><p className="text-xs font-medium">{item.title}</p><p className="mt-1 text-[11px] text-muted">{item.value}</p></div><span className="text-[10px] text-[#778780]">{item.page}</span></div>)}
        </div>
        <p className="mt-4 text-[10px] leading-5 text-muted">Illustration of the workflow. No sample financial values are shown.</p>
      </Surface>
    </section>
    <section id="how-it-works" className="border-y border-line bg-[#131b18]">
      <div className="mx-auto max-w-7xl px-5 py-16 sm:px-8"><Eyebrow>HOW IT WORKS</Eyebrow><div className="mt-3 flex flex-wrap items-end justify-between gap-4"><h2 className="max-w-lg text-3xl font-semibold tracking-[-.04em]">A practical way to inspect the details.</h2><p className="max-w-md text-sm leading-6 text-muted">BankEase AI currently uses deterministic extraction and calculations. It does not replace a lender, lawyer, or financial adviser.</p></div><div className="mt-8 grid gap-4 md:grid-cols-3">{steps.map(({ icon: Icon, number, title, text }) => <Surface key={number} className="p-5"><div className="flex items-center justify-between"><span className="grid size-10 place-items-center rounded-lg bg-teal/10 text-teal"><Icon size={19} /></span><span className="font-mono text-xs text-[#71817b]">{number}</span></div><h3 className="mt-5 text-sm font-semibold">{title}</h3><p className="mt-2 text-xs leading-6 text-muted">{text}</p></Surface>)}</div></div>
    </section>
    <footer className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-5 py-7 text-[11px] text-muted sm:px-8"><p>Informational and educational use only.</p><div className="flex gap-4"><Link href="/privacy" className="hover:text-ink">Privacy</Link><Link href="/terms" className="hover:text-ink">Terms</Link></div></footer>
  </main>;
}
