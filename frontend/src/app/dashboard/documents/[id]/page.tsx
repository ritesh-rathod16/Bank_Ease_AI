import type { Metadata } from "next";
import { DocumentDetail } from "@/features/documents/document-detail";

export const metadata: Metadata = { title: "Document review" };

export default async function DocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DocumentDetail id={id} />;
}
