import type { Metadata } from "next";
import { DocumentList } from "@/features/documents/document-list";

export const metadata: Metadata = { title: "Upload document" };

export default function UploadPage() {
  return <DocumentList initialView="upload" />;
}
