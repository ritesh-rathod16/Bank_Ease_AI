import { ExternalLink, FileText } from "lucide-react";
import { Surface } from "@/components/ui/surface";

export function SourcePreview({ id, filename, available }: Readonly<{
  id: string;
  filename: string;
  available?: boolean;
}>) {
  if (!available) return null;

  const previewUrl = `/api/v1/documents/${encodeURIComponent(id)}/preview`;
  const extension = filename.toLowerCase().split(".").pop();
  const isPdf = extension === "pdf";
  const isImage = ["png", "jpg", "jpeg", "tif", "tiff"].includes(extension ?? "");
  const canPreview = isPdf || isImage;

  return <Surface className="overflow-hidden">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
      <div className="flex items-center gap-2"><FileText className="text-teal" size={16} /><div><p className="text-xs font-semibold">Original source</p><p className="mt-1 text-[10px] text-muted">Private preview available to this account</p></div></div>
      <a className="inline-flex min-h-8 items-center gap-2 rounded-lg border border-line bg-panel-raised px-3 text-xs font-semibold text-ink hover:border-teal/30 hover:text-teal" href={previewUrl} target="_blank" rel="noreferrer"><ExternalLink size={13} />{canPreview ? "Open source" : "Download source"}</a>
    </div>
    {canPreview ? <iframe
      title={`Original source: ${filename}`}
      src={previewUrl}
      loading="lazy"
      referrerPolicy="no-referrer"
      className={`block w-full border-0 bg-white ${isPdf ? "h-[min(76vh,900px)] min-h-[480px]" : "h-[min(60vh,680px)] min-h-[320px]"}`}
    /> : <p className="px-5 py-4 text-xs text-muted">This original file format opens as a private download.</p>}
  </Surface>;
}
