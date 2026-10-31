"use client";

import { AssistantPanel } from "@/features/dashboard/assistant-panel";
import { api } from "@/lib/api/queries";
import { useQuery } from "@tanstack/react-query";

export function AssistantWorkspace({ requestedDocumentId }: { requestedDocumentId?: string }) {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  return <div className="h-[calc(100dvh-10.25rem)] max-h-[52rem] min-h-0 min-w-0">
    <AssistantPanel userName={me.data?.name ?? "there"} userId={me.data?.id ?? ""} requestedDocumentId={requestedDocumentId} />
  </div>;
}
