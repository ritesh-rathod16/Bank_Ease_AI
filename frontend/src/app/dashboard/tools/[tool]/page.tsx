import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DashboardTool } from "@/features/dashboard/dashboard-tool";

const tools = new Set(["calendar", "obligations", "meeting-prep", "glossary", "guides"]);
const titles: Record<string, string> = { calendar: "Financial calendar", obligations: "My obligations", "meeting-prep": "Meeting preparation", glossary: "Financial glossary", guides: "Guides & checklists" };
export async function generateMetadata({ params }: { params: Promise<{ tool: string }> }): Promise<Metadata> {
  const { tool } = await params;
  return { title: titles[tool] ?? "Financial tools" };
}

export default async function DashboardToolsPage({ params }: { params: Promise<{ tool: string }> }) {
  const { tool } = await params;
  if (!tools.has(tool)) notFound();
  return <DashboardTool tool={tool} />;
}
