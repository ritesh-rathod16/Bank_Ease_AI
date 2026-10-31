import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = { title: "Privacy" };

export default function PrivacyPage() {
  return <main className="mx-auto min-h-screen max-w-3xl px-5 py-12 sm:px-8">
    <Link className="text-xs text-teal hover:underline" href="/">← BankEase AI</Link>
    <p className="mt-10 text-[10px] font-bold uppercase tracking-[.16em] text-teal">PRIVACY INFORMATION</p>
    <h1 className="mt-3 text-3xl font-semibold">How this prototype handles data</h1>
    <div className="mt-6 space-y-5 text-sm leading-7 text-muted">
      <p>BankEase AI stores accepted original PDFs, scanned images, CSV, and XLSX files in a private local uploads directory, separate from the frontend. Extracted text and analysis records are kept for the signed-in account. Source previews and downloads require an authenticated session and an owner check.</p>
      <p>Files and records are not encrypted at rest. The default 24-hour retention threshold is checked only when that same account uploads another document; it is not a background cleanup service. You can delete a document or account from the app. Account export and deletion controls are available in settings.</p>
      <p>The prototype has no production email provider, production deployment, or verified Windows file ACL configuration. Do not upload real customer agreements or other sensitive financial documents. This page describes current prototype behavior and is not a regulatory privacy notice.</p>
    </div>
    <Link className="mt-8 inline-block text-xs text-teal hover:underline" href="/terms">Read terms and limitations</Link>
  </main>;
}
