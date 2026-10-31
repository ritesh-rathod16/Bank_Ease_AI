import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DocumentLibrary } from "@/features/dashboard/document-library";

export const metadata: Metadata = { title: "Document library" };

export default async function LibraryPage({ params, searchParams }: { params: Promise<{ category: string }>; searchParams: Promise<{ type?: string }> }) {
  const [{ category }, query] = await Promise.all([params, searchParams]);
  if (category !== "banking" && category !== "insurance" && category !== "contracts" && category !== "identity") notFound();
  return <DocumentLibrary kind={category} initialFilter={query.type} />;
}
