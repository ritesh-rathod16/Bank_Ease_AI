"use client";

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { StatementTransaction, TransactionCorrectionFields, TransactionCorrectionRequest } from "@/types/api";
import { humanize } from "@/lib/utils";

function money(minor: number | null, currency: string) {
  if (minor === null) return "Not available";
  const amount = minor / 100;
  if (currency === "UNSPECIFIED") return amount.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  try {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency }).format(amount);
  } catch {
    return `${currency} ${amount.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
}

function moneyInput(minor: number | null) {
  if (minor === null) return "";
  const value = Math.abs(minor);
  return `${minor < 0 ? "-" : ""}${Math.floor(value / 100)}.${String(value % 100).padStart(2, "0")}`;
}

function parseMinor(input: string) {
  const value = input.trim();
  if (!value) return null;
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(value);
  if (!match) throw new Error("Enter an amount with up to two decimal places.");
  const whole = Number(match[2]);
  const cents = Number((match[3] ?? "").padEnd(2, "0"));
  const unsigned = whole * 100 + cents;
  if (!Number.isSafeInteger(unsigned)) throw new Error("Amount is outside the supported exact range.");
  return match[1] ? -unsigned : unsigned;
}

type Draft = { date: string; description: string; amount: string; balance: string; reason: string };
type Save = (rowNumber: number, body: TransactionCorrectionRequest) => Promise<unknown>;

export function StatementTransactionRow({
  row,
  currency,
  saving,
  onSave,
  onReview,
  reviewing,
}: Readonly<{ row: StatementTransaction; currency: string; saving: boolean; onSave: Save; onReview: (rowNumber: number) => Promise<unknown>; reviewing: boolean }>) {
  const current = row.effective ?? row;
  const corrected = Boolean(row.user_correction);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>({ date: "", description: "", amount: "", balance: "", reason: "" });
  const [error, setError] = useState("");

  function edit() {
    setDraft({
      date: current.date,
      description: current.description,
      amount: moneyInput(current.amount_minor),
      balance: moneyInput(current.balance_minor),
      reason: "",
    });
    setError("");
    setEditing(true);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      const changes: TransactionCorrectionFields = {};
      if (draft.date !== current.date) changes.date = draft.date || null;
      const description = draft.description.trim();
      if (description !== current.description) changes.description = description || null;
      const amount = parseMinor(draft.amount);
      if (amount !== current.amount_minor) changes.amount_minor = amount;
      const balance = parseMinor(draft.balance);
      if (balance !== current.balance_minor) changes.balance_minor = balance;
      if (!Object.keys(changes).length) {
        setEditing(false);
        return;
      }
      await onSave(row.row_number, {
        ...changes,
        expected_revision: row.correction_revision ?? 0,
        reason: draft.reason.trim(),
      });
      setEditing(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save this correction.");
    }
  }

  async function revert() {
    const fields = row.user_correction?.fields;
    if (!fields) return;
    const body: TransactionCorrectionRequest = {
      expected_revision: row.correction_revision ?? 0,
      reason: "Reverted to the imported source values.",
    };
    for (const field of Object.keys(fields) as Array<keyof TransactionCorrectionFields>) {
      if (field === "date") body.date = null;
      if (field === "description") body.description = null;
      if (field === "amount_minor") body.amount_minor = null;
      if (field === "balance_minor") body.balance_minor = null;
    }
    setError("");
    try {
      await onSave(row.row_number, body);
      setEditing(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not revert this correction.");
    }
  }

  function changedValue(field: string, value: string | number | null | undefined) {
    if (value === null || value === undefined) return "Imported value";
    if (field === "amount_minor" || field === "balance_minor") return money(value as number, currency);
    return String(value);
  }

  return <>
    <tr className="align-top hover:bg-slate-50">
      <td className="px-4 py-3 tabular-nums text-muted">{row.page ? `Page ${row.page}` : row.source_sheet ? `${row.source_sheet}, row ${row.row_number}` : `CSV row ${row.row_number}`}{row.extraction_method === "pymupdf_tesseract_ocr" && <p className="mt-1 text-[9px] font-semibold text-amber-700">OCR - verify source</p>}</td>
      <td className="whitespace-nowrap px-4 py-3 tabular-nums">{current.date}{corrected && <p className="mt-1 text-[10px] text-teal">Corrected</p>}</td>
      <td className="max-w-[360px] whitespace-normal px-4 py-3 leading-5">
        <span>{current.description}</span>
        {row.evidence && <p className="mt-2 border-l border-teal/40 pl-2 text-[10px] leading-4 text-muted">{row.evidence}</p>}
        {row.evidence_status && <p className="mt-1 text-[10px] leading-4 text-muted">{row.verification_status === "verified" ? "Page citation matched source text" : row.evidence_status === "source_cells_available" ? "Original source cells retained" : row.evidence_status === "matched" ? "Citation text located; review the original scan" : "Source citation needs review"}</p>}
        <details className="mt-2 text-[10px] text-muted"><summary className="cursor-pointer hover:text-teal">Original source values</summary><dl className="mt-2 grid gap-1">{Object.entries(row.source_values).map(([key, value]) => <div key={key} className="grid grid-cols-[auto_1fr] gap-2"><dt className="font-medium">{key}</dt><dd className="break-all">{value || "—"}</dd></div>)}</dl></details>
        {!!row.correction_history?.length && <details className="mt-2 text-[10px] text-muted"><summary className="cursor-pointer hover:text-teal">Correction history ({row.correction_history.length})</summary><ol className="mt-2 space-y-2">{row.correction_history.map((event) => <li key={event.id} className="border-l border-line pl-2"><p className="font-medium text-ink">Revision {event.revision} · {event.action === "set" ? "Correction saved" : "Reverted"}</p>{Object.keys(event.after).map((field) => <p key={field}>{humanize(field)}: {changedValue(field, event.before[field as keyof TransactionCorrectionFields])} → {changedValue(field, event.after[field as keyof TransactionCorrectionFields])}</p>)}{event.reason && <p>{event.reason}</p>}</li>)}</ol></details>}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">{current.debit_minor ? money(current.debit_minor, currency) : "—"}</td>
      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">{current.credit_minor ? money(current.credit_minor, currency) : "—"}</td>
      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">{money(current.balance_minor, currency)}</td>
      <td className="px-3 py-3 text-right"><div className="flex flex-col items-end gap-1"><Button size="sm" variant="ghost" onClick={edit}>Edit</Button><Button size="sm" variant="ghost" disabled={reviewing} onClick={() => void onReview(row.row_number)}>{reviewing ? "Sending…" : "Request review"}</Button></div></td>
    </tr>
    {editing && <tr><td colSpan={7} className="border-t border-line bg-white px-4 py-4">
      <form className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" onSubmit={submit}>
        <label className="grid gap-1 text-[10px] text-muted">Date<Input required type="date" value={draft.date} onChange={(event) => setDraft({ ...draft, date: event.target.value })} /></label>
        <label className="grid gap-1 text-[10px] text-muted">Description<Input required maxLength={500} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
        <label className="grid gap-1 text-[10px] text-muted">Signed amount<Input inputMode="decimal" placeholder="-1250.00" value={draft.amount} onChange={(event) => setDraft({ ...draft, amount: event.target.value })} /></label>
        <label className="grid gap-1 text-[10px] text-muted">Running balance<Input inputMode="decimal" placeholder="Optional" value={draft.balance} onChange={(event) => setDraft({ ...draft, balance: event.target.value })} /></label>
        <label className="grid gap-1 text-[10px] text-muted">Reason (optional)<Input maxLength={500} value={draft.reason} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} /></label>
        <div className="flex flex-wrap items-center gap-2 sm:col-span-2 xl:col-span-5">
          <Button size="sm" disabled={saving}>{saving ? "Saving…" : "Save correction"}</Button>
          {corrected && <Button type="button" size="sm" variant="secondary" disabled={saving} onClick={revert}>Revert all corrections</Button>}
          <Button type="button" size="sm" variant="ghost" disabled={saving} onClick={() => { setEditing(false); setError(""); }}>Cancel</Button>
          <p className="text-[10px] leading-4 text-muted">The imported row and its evidence stay unchanged. New comparisons use these corrected values.</p>
        </div>
        {error && <p role="alert" className="text-xs text-red-700 sm:col-span-2 xl:col-span-5">{error}</p>}
      </form>
    </td></tr>}
  </>;
}
