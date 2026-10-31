import type { Metadata } from "next";
import { CompareWorkspace } from "@/features/comparison/compare-workspace";
export const metadata: Metadata = { title: "Compare loans" };
export default function ComparePage() { return <CompareWorkspace />; }
