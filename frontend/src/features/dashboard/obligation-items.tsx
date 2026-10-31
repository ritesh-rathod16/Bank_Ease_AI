import Link from "next/link";
import { FileText } from "lucide-react";
import { formatCurrency, formatDate } from "@/lib/utils";
import type { UpcomingObligation } from "@/types/api";

function dueIn(value: string) {
  const due = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const days = Math.round((due.getTime() - today) / 86_400_000);
  return !Number.isFinite(days) ? "Date unavailable" : days < 0 ? "Date passed" : days === 0 ? "Due today" : `Due in ${days} day${days === 1 ? "" : "s"}`;
}

export function ObligationItems({ obligations, compact = false }: {
  obligations: UpcomingObligation[];
  compact?: boolean;
}) {
  return <ol className={`divide-y divide-line ${compact ? "mt-3" : "mt-4"}`}>
    {obligations.map((item) => <li key={`${item.document_id}-${item.title}-${item.due_date}`} className={compact ? "py-3" : "flex flex-wrap items-start justify-between gap-3 py-4"}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={compact ? "text-xs font-semibold" : "text-sm font-semibold"}>{item.title}</p>
          <p className="mt-1 text-[10px] tabular-nums text-slate-600">{formatDate(item.due_date)} | {dueIn(item.due_date)} | Confirmed by you</p>
          {item.amount != null && <p className="mt-1 text-[10px] text-muted">{item.amount_label ?? "Stated amount"}: {formatCurrency(item.amount)}{item.amount_page ? ` | p. ${item.amount_page}` : ""}</p>}
        </div>
        {compact && <Link href={`/dashboard/documents/${item.document_id}`} aria-label={`View ${item.filename}, page ${item.date_page}`} className="shrink-0 rounded-md px-2 py-1 text-[10px] font-semibold text-blue-700 hover:bg-blue-50">View source</Link>}
      </div>
      <p className={`mt-1 leading-4 text-muted ${compact ? "line-clamp-2 text-[10px]" : "max-w-2xl text-[11px]"}`}>Page {item.date_page}: {item.date_evidence}</p>
      {item.date_note && <p className={`mt-1 leading-4 text-amber-800 ${compact ? "line-clamp-2 text-[9px]" : "max-w-2xl text-[10px]"}`}>{item.date_note}</p>}
      {!compact && item.amount_evidence && <p className="mt-1 max-w-2xl text-[10px] leading-4 text-muted">Amount evidence, page {item.amount_page ?? "not recorded"}: {item.amount_evidence}</p>}
      {!compact && <div className="mt-2 text-[10px] text-muted"><span>{item.filename}</span><Link href={`/dashboard/documents/${item.document_id}`} className="ml-3 inline-flex items-center gap-1 font-semibold text-blue-700 hover:underline"><FileText size={12} />View source</Link></div>}
    </li>)}
  </ol>;
}
