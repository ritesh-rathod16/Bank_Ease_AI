"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { FileSearch, LoaderCircle, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api/queries";
import { humanize } from "@/lib/utils";

export function GlobalSearch({ triggerClassName = "" }: { triggerClassName?: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        const target = event.target;
        if (target instanceof HTMLElement && target.closest("input, textarea, select, [contenteditable='true']")) return;
        event.preventDefault();
        setOpen(true);
      }
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(value.trim()), 220);
    return () => window.clearTimeout(timer);
  }, [value]);
  const results = useQuery({ queryKey: ["global-search", query], queryFn: () => api.search(query), enabled: open && query.length >= 2, staleTime: 10_000 });

  function visit(documentId: string) {
    setOpen(false);
    router.push(`/dashboard/documents/${encodeURIComponent(documentId)}`);
  }

  return <>
    <button type="button" onClick={() => setOpen(true)} className={`flex h-10 w-full max-w-[620px] items-center gap-3 rounded-lg border border-line bg-white px-3 text-left text-xs text-muted shadow-sm transition hover:border-blue-300 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/30 ${triggerClassName}`} aria-label="Search your documents, clauses, and amounts">
      <Search size={15} /><span className="min-w-0 flex-1 truncate">Search across your documents, clauses, amounts, or ask a question...</span><kbd className="hidden rounded border border-line bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-500 sm:inline">Ctrl/⌘ K</kbd>
    </button>
    {open && <div className="fixed inset-0 z-50 grid items-start justify-center bg-slate-950/30 px-3 pt-[12vh] backdrop-blur-[2px]" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
      <section role="dialog" aria-modal="true" aria-label="Search your documents" className="w-full max-w-2xl overflow-hidden rounded-xl border border-line bg-white shadow-2xl shadow-slate-900/20">
        <div className="flex items-center gap-3 border-b border-line px-4"><Search className="shrink-0 text-teal" size={18} /><Input autoFocus className="h-14 border-0 px-0 shadow-none focus:ring-0" value={value} onChange={(event) => setValue(event.target.value.slice(0, 120))} placeholder="Search filenames, clauses, amounts, or transactions" aria-label="Search query" /><Button variant="ghost" size="icon" aria-label="Close search" onClick={() => setOpen(false)}><X size={16} /></Button></div>
        <div className="max-h-[55vh] overflow-y-auto p-2">
          {value.trim().length < 2 && <p className="px-3 py-5 text-xs text-muted">Enter at least two characters. Search is limited to documents in your account.</p>}
          {results.isFetching && <p className="flex items-center gap-2 px-3 py-5 text-xs text-muted"><LoaderCircle size={14} className="animate-spin" />Searching your documents…</p>}
          {results.isError && <p role="alert" className="px-3 py-5 text-xs text-red-700">{results.error.message}</p>}
          {results.data?.results.length === 0 && !results.isFetching && <p className="px-3 py-5 text-xs text-muted">No matching document evidence was found.</p>}
          {results.data?.results.map((result, index) => <button type="button" key={`${result.document_id}-${result.kind}-${result.page}-${index}`} onClick={() => visit(result.document_id)} className="flex w-full gap-3 rounded-lg px-3 py-3 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/30">
            <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-blue-50 text-teal"><FileSearch size={16} /></span><span className="min-w-0 flex-1"><span className="flex flex-wrap items-center gap-2 text-xs font-semibold text-ink"><span className="truncate">{result.filename}</span><span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-medium text-slate-600">{humanize(result.kind)}{result.location ? ` | ${result.location}` : result.page ? ` | p. ${result.page}` : ""}</span></span><span className="mt-1 block line-clamp-2 text-[11px] leading-5 text-muted">{result.excerpt}</span></span>
          </button>)}
        </div>
        <div className="border-t border-line bg-slate-50 px-4 py-2 text-[10px] text-muted">Search results use owner-authorized source text and extracted evidence.</div>
      </section>
    </div>}
  </>;
}
