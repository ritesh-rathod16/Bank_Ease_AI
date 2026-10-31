"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileSearch, LoaderCircle, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Surface, Eyebrow } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import { humanize } from "@/lib/utils";

export function DashboardSearch({ initialQuery = "" }: { initialQuery?: string }) {
  const [value, setValue] = useState(initialQuery);
  const [query, setQuery] = useState(initialQuery);
  useEffect(() => { const timer = window.setTimeout(() => setQuery(value.trim()), 220); return () => window.clearTimeout(timer); }, [value]);
  const results = useQuery({ queryKey: ["global-search", query], queryFn: () => api.search(query), enabled: query.length >= 2, staleTime: 10_000 });
  return <div className="space-y-5">
    <section><Eyebrow>SEARCH YOUR ACCOUNT</Eyebrow><h1 className="mt-2 text-2xl font-semibold tracking-tight">Search across documents</h1><p className="mt-1 text-sm text-muted">Search filenames, extracted values, transaction descriptions, source passages, and matched clauses in your own documents.</p></section>
    <label className="relative block"><Search className="absolute left-3 top-3 text-muted" size={16} /><Input value={value} onChange={(event) => setValue(event.target.value.slice(0, 120))} className="h-11 pl-10" placeholder="Try a fee, amount, date, or clause phrase" aria-label="Search across your documents" /></label>
    <Surface className="overflow-hidden shadow-sm">
      <div className="border-b border-line px-4 py-3 text-xs font-semibold">{query.length < 2 ? "Enter at least two characters" : results.isFetching ? "Searching authorized document evidence…" : `${results.data?.results.length ?? 0} result${results.data?.results.length === 1 ? "" : "s"}`}</div>
      {results.isFetching && <div className="flex items-center gap-2 p-5 text-xs text-muted"><LoaderCircle size={14} className="animate-spin" />Searching…</div>}
      {results.isError && <p role="alert" className="p-5 text-xs text-red-700">{results.error.message}</p>}
      {results.data?.results.length === 0 && !results.isFetching && <p className="p-5 text-xs text-muted">No source text or extracted evidence matched this search.</p>}
      {results.data?.results.map((result, index) => <Link href={`/dashboard/documents/${result.document_id}`} key={`${result.document_id}-${result.kind}-${result.page}-${index}`} className="flex gap-3 border-b border-line p-4 transition last:border-0 hover:bg-slate-50"><span className="grid size-9 shrink-0 place-items-center rounded-lg bg-blue-50 text-blue-700"><FileSearch size={16} /></span><span className="min-w-0 flex-1"><span className="flex flex-wrap items-center gap-2"><strong className="truncate text-xs">{result.filename}</strong><span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] text-slate-600">{humanize(result.kind)}{result.location ? ` | ${result.location}` : result.page ? ` | page ${result.page}` : ""}</span></span><span className="mt-1 block text-[10px] text-muted">{humanize(result.doc_type)}</span><span className="mt-2 block text-xs leading-5 text-slate-700">{result.excerpt}</span></span></Link>)}
    </Surface>
    <p className="text-[10px] text-muted">Results cite stored source passages and extraction evidence. Search never includes other accounts’ documents.</p>
  </div>;
}
