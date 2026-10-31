"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitCompareArrows, LoaderCircle, UploadCloud } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import type { CompareRow, StatementComparisonResult } from "@/types/api";

const loanDocumentTypes = new Set(["loan_agreement", "kfs", "sanction_letter", "repayment_schedule", "fee_schedule", "annexure"]);

type Comparison =
  | { kind: "loan"; result: { rows: CompareRow[] } }
  | { kind: "statement"; result: StatementComparisonResult };

function Evidence({ value }: { value: { value: string | number | null; page: number | null; evidence: string | null } }) {
  return <div className="mt-1 text-[10px] leading-4 text-muted">
    <span>{value.value === null ? "Not found" : String(value.value)}{value.page ? ` · page ${value.page}` : ""}</span>
    {value.evidence && <p className="mt-1">{value.evidence}</p>}
  </div>;
}

export function VersionHistory({ id, docType }: Readonly<{
  id: string;
  docType: string;
}>) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [compareWith, setCompareWith] = useState("");
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const history = useQuery({ queryKey: ["document-versions", id], queryFn: () => api.versions(id) });
  const versions = history.data?.versions ?? [];
  const alternatives = versions.filter((version) => version.id !== id);
  const selectedId = alternatives.some((version) => version.id === compareWith)
    ? compareWith
    : alternatives[0]?.id ?? "";
  const latest = versions.at(-1);
  const canCreateRevision = latest?.id === id;
  const isStatement = docType.startsWith("bank_statement_");
  const canCompareVersions = isStatement || loanDocumentTypes.has(docType);

  const upload = useMutation({
    mutationFn: (selectedFile: File) => api.uploadRevision(selectedFile, id),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      toast.success(result.status === "processing" ? `Version ${result.version_number} queued for analysis.` : `Saved version ${result.version_number}.`);
      router.push(`/dashboard/documents/${result.id}`);
    },
    onError: (error) => toast.error(error.message),
  });
  const compare = useMutation({
    mutationFn: async (otherId: string): Promise<Comparison> => isStatement
      ? { kind: "statement", result: await api.compareStatements(id, otherId) }
      : { kind: "loan", result: await api.compare(id, otherId) },
    onSuccess: async (result) => {
      setComparison(result);
      await queryClient.invalidateQueries({ queryKey: ["comparisons"] });
      toast.success("Version comparison saved with source evidence.");
    },
    onError: (error) => toast.error(error.message),
  });

  function submitRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      toast.error("Choose the revised source file first.");
      return;
    }
    upload.mutate(file);
  }

  return <Surface className="overflow-hidden">
    <div className="border-b border-line px-5 py-4"><Eyebrow>DOCUMENT VERSIONS</Eyebrow><p className="mt-1 text-xs text-muted">Keep revised copies together and compare their extracted evidence.</p></div>
    {history.isLoading && <div className="flex items-center gap-2 px-5 py-4 text-xs text-muted"><LoaderCircle className="animate-spin" size={14} />Loading version history</div>}
    {history.isError && <p role="alert" className="px-5 py-4 text-xs text-red-700">{history.error.message}</p>}
    {history.data && <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_minmax(260px,.8fr)]">
      <div>
        <ol className="space-y-2">
          {versions.map((version) => <li key={version.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-slate-50 px-3 py-2.5">
            <div><p className="text-xs font-semibold">Version {version.version_number}{version.id === id ? " · viewing" : ""}</p><p className="mt-1 text-[10px] text-muted">{version.filename} · {version.page_count} pages{version.transaction_count != null ? ` · ${version.transaction_count} rows` : ""}</p></div>
            {version.id !== id && <Link className="text-[10px] text-teal hover:underline" href={`/dashboard/documents/${version.id}`}>Open</Link>}
          </li>)}
        </ol>
        {alternatives.length > 0 && canCompareVersions && <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_auto]">
          <label className="grid gap-1 text-[10px] font-medium text-muted">Compare this version with
            <select className="h-10 w-full rounded-lg border border-line bg-white px-3 text-xs text-ink" value={selectedId} onChange={(event) => { setCompareWith(event.target.value); setComparison(null); }}>
              {alternatives.map((version) => <option key={version.id} value={version.id}>Version {version.version_number} · {version.filename}</option>)}
            </select>
          </label>
          <Button className="self-end" disabled={!selectedId || compare.isPending} onClick={() => compare.mutate(selectedId)}>{compare.isPending ? <LoaderCircle className="animate-spin" size={14} /> : <GitCompareArrows size={14} />}Compare versions</Button>
        </div>}
        {alternatives.length > 0 && !canCompareVersions && <p className="mt-3 text-[10px] leading-4 text-muted">Revision comparison is not implemented for this document type yet.</p>}
      </div>
      <div className="rounded-lg border border-line bg-white p-4">
        <p className="text-xs font-semibold">Add a revised copy</p>
        {!canCreateRevision && latest && <p className="mt-2 text-[10px] leading-4 text-muted">Open version {latest.version_number} to add the next revision.</p>}
        {canCreateRevision && <form className="mt-3 grid gap-3" onSubmit={submitRevision}>
          <Input aria-label="Revised document file" type="file" accept={isStatement ? "application/pdf,.pdf,text/csv,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.xlsx,image/png,.png,image/jpeg,.jpg,.jpeg,image/tiff,.tif,.tiff" : "application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx,image/png,.png,image/jpeg,.jpg,.jpeg,image/tiff,.tif,.tiff"} onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          <Button className="w-fit" disabled={!file || upload.isPending}>{upload.isPending ? <LoaderCircle className="animate-spin" size={14} /> : <UploadCloud size={14} />}Upload revision</Button>
        </form>}
      </div>
      {comparison?.kind === "loan" && <div className="overflow-x-auto lg:col-span-2">
        <table className="w-full min-w-[620px] text-left text-xs"><thead className="text-[10px] uppercase tracking-wider text-muted"><tr><th className="px-3 py-2">Field</th><th className="px-3 py-2">This version</th><th className="px-3 py-2">Compared version</th><th className="px-3 py-2">Result</th></tr></thead><tbody className="divide-y divide-line">{comparison.result.rows.map((row) => <tr key={row.field} className="align-top"><th className="px-3 py-3 font-medium">{row.field.replaceAll("_", " ")}</th><td className="px-3 py-3"><Evidence value={row.a} /></td><td className="px-3 py-3"><Evidence value={row.b} /></td><td className="px-3 py-3 text-muted">{row.status.replaceAll("_", " ")}</td></tr>)}</tbody></table>
      </div>}
      {comparison?.kind === "statement" && <div className="grid gap-2 lg:col-span-2">
        <p className="text-xs leading-5 text-muted">{comparison.result.note}</p>
        <p className="text-xs font-semibold">{comparison.result.summary.difference_count} transaction or balance differences</p>
        {comparison.result.differences.slice(0, 10).map((difference, index) => <div key={`${difference.kind}-${difference.date}-${index}`} className="rounded-lg border border-line bg-slate-50 p-3 text-xs"><span className="font-medium">{difference.kind.replaceAll("_", " ")}</span><p className="mt-1 text-muted">{difference.date} · {difference.description}</p>{difference.a?.evidence && <p className="mt-1 text-[10px] text-muted">This version: {difference.a.evidence}</p>}{difference.b?.evidence && <p className="mt-1 text-[10px] text-muted">Compared version: {difference.b.evidence}</p>}</div>)}
      </div>}
    </div>}
  </Surface>;
}
