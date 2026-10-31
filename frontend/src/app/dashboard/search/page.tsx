import type { Metadata } from "next";
import { DashboardSearch } from "@/features/dashboard/dashboard-search";

export const metadata: Metadata = { title: "Search documents" };

export default async function SearchPage({ searchParams }: { searchParams: Promise<{ q?: string }> }) {
  const params = await searchParams;
  return <DashboardSearch initialQuery={params.q?.slice(0, 120) ?? ""} />;
}
