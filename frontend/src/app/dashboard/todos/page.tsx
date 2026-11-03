import type { Metadata } from "next";
import { TodoWorkspace } from "@/features/dashboard/todo-workspace";

export const metadata: Metadata = { title: "Todo board" };

export default function TodosPage() {
  return <TodoWorkspace />;
}
