import { ExternalLink, FileText, Printer } from "lucide-react";
import { Surface } from "@/components/ui/surface";

export function SourcePreview({
  id,
  filename,
  available,
  title = "Original source",
  subtitle = "Private preview available to this account",
  openLabel,
  previewUrl,
  downloadUrl,
  showPrint = false,
  onPrint,
  printLabel = "Print review",
}: Readonly<{
  id?: string;
  filename: string;
  available?: boolean;
  title?: string;
  subtitle?: string;
  openLabel?: string;
  previewUrl?: string;
  downloadUrl?: string;
  showPrint?: boolean;
  onPrint?: () => void;
  printLabel?: string;
}>) {
  if (!available) return null;

  const resolvedPreviewUrl = previewUrl ?? (id ? `/api/v1/documents/${encodeURIComponent(id)}/preview` : undefined);
  const resolvedDownloadUrl = downloadUrl ?? resolvedPreviewUrl ?? "";
  if (!resolvedPreviewUrl) return null;

  const extension = filename.toLowerCase().split(".").pop();
  const isPdf = extension === "pdf";
  const isImage = ["png", "jpg", "jpeg", "tif", "tiff"].includes(extension ?? "");
  const canPreview = isPdf || isImage;
  const isBlobDownload = resolvedDownloadUrl.startsWith("blob:");

  return <Surface className="overflow-hidden border border-slate-200 bg-slate-50 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 py-4">
      <div className="flex items-center gap-2"><FileText className="text-blue-600" size={16} /><div><p className="text-xs font-semibold text-slate-900">{title}</p><p className="mt-1 text-[10px] text-slate-500">{subtitle}</p></div></div>
      <div className="flex flex-wrap items-center gap-2">
        {showPrint && onPrint && <button type="button" onClick={onPrint} className="inline-flex min-h-8 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 text-xs font-semibold text-slate-700 transition hover:border-blue-300 hover:text-blue-700"><Printer size={13} />{printLabel}</button>}
        <a className="inline-flex min-h-8 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 text-xs font-semibold text-slate-700 transition hover:border-blue-300 hover:text-blue-700" href={resolvedDownloadUrl || undefined} target={isBlobDownload ? undefined : "_blank"} rel={isBlobDownload ? undefined : "noreferrer"} download={isBlobDownload ? filename : undefined}>{canPreview ? <ExternalLink size={13} /> : <ExternalLink size={13} />}{openLabel ?? (canPreview ? "Open source" : "Download source")}</a>
      </div>
    </div>
    {canPreview ? <iframe
      title={`${title}: ${filename}`}
      src={resolvedPreviewUrl}
      loading="lazy"
      referrerPolicy="no-referrer"
      className={`block w-full border-0 bg-white ${isPdf ? "h-[min(76vh,900px)] min-h-[480px]" : "h-[min(60vh,680px)] min-h-[320px]"}`}
    /> : <p className="px-5 py-4 text-xs text-slate-500">This original file format opens as a private download.</p>}
  </Surface>;
}
