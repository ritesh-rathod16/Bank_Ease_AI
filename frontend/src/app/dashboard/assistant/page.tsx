import type { Metadata } from "next";
import { AssistantWorkspace } from "@/features/dashboard/assistant-workspace";

export const metadata: Metadata = { title: "AI Assistant" };

export default async function AssistantPage({ searchParams }: { searchParams: Promise<{ document_id?: string | string[]; ask?: string | string[] }> }) {
  const params = await searchParams;
  const documentId = Array.isArray(params.document_id) ? params.document_id[0] : params.document_id;
  const question = Array.isArray(params.ask) ? params.ask[0] : params.ask;
  return <AssistantWorkspace requestedDocumentId={documentId} requestedQuestion={question} />;
}
