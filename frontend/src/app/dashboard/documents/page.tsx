import type { Metadata } from "next";
import { DocumentList } from "@/features/documents/document-list";
export const metadata: Metadata = { title: "My documents" };
export default function DocumentsPage() { return <DocumentList />; }
