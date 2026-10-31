"use client";

import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Clock3, LoaderCircle, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import { formatDate } from "@/lib/utils";
import type { SavedComparison } from "@/types/api";

export function ComparisonHistory({ kind, onOpen }: {
  kind: "loan" | "statement" | "financial_statement";
  onOpen: (comparison: SavedComparison) => void;
}) {
  const queryClient = useQueryClient();
  const history = useInfiniteQuery({
    queryKey: ["comparisons", kind],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api.comparisons(kind, pageParam),
    getNextPageParam: (page) => page.has_more ? page.offset + page.limit : undefined,
  });
  const open = useMutation({
    mutationFn: (id: string) => api.comparison(id),
    onSuccess: onOpen,
    onError: (error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.removeComparison(id),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["comparisons", kind] }); toast.success("Saved comparison deleted."); },
    onError: (error) => toast.error(error.message),
  });
  const comparisons = history.data?.pages.flatMap((page) => page.comparisons) ?? [];

  return <Surface className="p-5 sm:p-6">
    <div className="flex items-center gap-2"><Clock3 className="text-teal" size={16} /><Eyebrow>SAVED COMPARISONS</Eyebrow></div>
    <p className="mt-2 text-xs leading-5 text-muted">Reopen a previous comparison or remove its saved result. Deleting a source document also removes comparisons that reference it.</p>
    {history.isLoading && <p className="mt-4 text-xs text-muted">Loading comparison history…</p>}
    {history.isError && <p className="mt-4 text-xs text-red-700" role="alert">{history.error.message}</p>}
    {!history.isLoading && !history.isError && comparisons.length === 0 && <p className="mt-4 rounded-lg border border-line bg-slate-50 p-4 text-xs text-muted">No saved comparisons yet.</p>}
    {comparisons.length > 0 && <ul className="mt-4 divide-y divide-line rounded-lg border border-line">{comparisons.map((comparison) => {
      const count = kind === "financial_statement"
        ? (comparison.summary.difference_count ?? 0) + (comparison.summary.needs_review_count ?? 0) + (comparison.summary.not_comparable_count ?? 0)
        : comparison.summary.difference_count ?? 0;
      const countLabel = kind === "loan" ? `${count} term rows to review` : kind === "financial_statement" ? `${count} financial totals to review` : `${count} statement differences`;
      return <li key={comparison.id} className="flex flex-wrap items-center justify-between gap-3 p-3 sm:px-4">
        <div className="min-w-0"><p className="break-words text-xs font-semibold">{comparison.title}</p><p className="mt-1 text-[10px] text-muted">{formatDate(comparison.created_at)} · {countLabel}{comparison.history_truncated ? " · saved excerpt" : ""}</p></div>
        <div className="flex shrink-0 gap-1"><Button size="sm" variant="secondary" disabled={open.isPending} onClick={() => open.mutate(comparison.id)} aria-label={`Open ${comparison.title}`}><RotateCcw size={13} />Open</Button><Button size="sm" variant="ghost" disabled={remove.isPending} onClick={() => { if (window.confirm("Delete this saved comparison? This removes the stored result snapshot.")) remove.mutate(comparison.id); }} aria-label={`Delete ${comparison.title}`}><Trash2 size={13} /></Button></div>
      </li>;
    })}</ul>}
    {history.hasNextPage && <Button className="mt-3" size="sm" variant="ghost" disabled={history.isFetchingNextPage} onClick={() => history.fetchNextPage()}>{history.isFetchingNextPage && <LoaderCircle className="animate-spin" size={13} />}Load more</Button>}
  </Surface>;
}
