"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Circle, Plus, Sparkles, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import { useQuery } from "@tanstack/react-query";

type TodoStatus = "backlog" | "doing" | "done";

type TodoItem = {
  id: string;
  title: string;
  description: string;
  owner: string;
  priority: "low" | "medium" | "high";
  status: TodoStatus;
  dueDate?: string;
  createdAt: string;
};

const statusOrder: TodoStatus[] = ["backlog", "doing", "done"];
const statusLabels: Record<TodoStatus, string> = {
  backlog: "Backlog",
  doing: "In progress",
  done: "Done",
};

const priorityStyles = {
  low: "bg-emerald-50 text-emerald-700 border-emerald-200",
  medium: "bg-amber-50 text-amber-700 border-amber-200",
  high: "bg-red-50 text-red-700 border-red-200",
};

export function TodoWorkspace() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const userName = me.data?.name || "User";
  const userKey = me.data?.id || "guest";
  const [items, setItems] = useState<TodoItem[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [owner, setOwner] = useState(userName);
  const [priority, setPriority] = useState<TodoItem["priority"]>("medium");
  const [dueDate, setDueDate] = useState("");

  useEffect(() => {
    setOwner(userName);
  }, [userName]);

  useEffect(() => {
    if (!userKey) return;
    try {
      const raw = window.localStorage.getItem(`bankease-ai-todos-${userKey}`);
      if (raw) {
        const parsed = JSON.parse(raw) as TodoItem[];
        if (Array.isArray(parsed)) setItems(parsed);
      }
    } catch {
      setItems([]);
    }
  }, [userKey]);

  useEffect(() => {
    if (!userKey) return;
    window.localStorage.setItem(`bankease-ai-todos-${userKey}`, JSON.stringify(items));
  }, [items, userKey]);

  const grouped = useMemo(() => ({
    backlog: items.filter((item) => item.status === "backlog"),
    doing: items.filter((item) => item.status === "doing"),
    done: items.filter((item) => item.status === "done"),
  }), [items]);

  function addTask(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedTitle = title.trim();
    if (!trimmedTitle) return;
    const record: TodoItem = {
      id: crypto.randomUUID(),
      title: trimmedTitle,
      description: description.trim(),
      owner: owner.trim() || userName,
      priority,
      status: "backlog",
      dueDate: dueDate || undefined,
      createdAt: new Date().toISOString(),
    };
    setItems((current) => [record, ...current]);
    setTitle("");
    setDescription("");
    setPriority("medium");
    setDueDate("");
    setOwner(userName);
  }

  function moveTask(id: string, direction: -1 | 1) {
    setItems((current) => current.map((item) => {
      if (item.id !== id) return item;
      const index = statusOrder.indexOf(item.status);
      const nextIndex = Math.min(statusOrder.length - 1, Math.max(0, index + direction));
      return { ...item, status: statusOrder[nextIndex] };
    }));
  }

  function deleteTask(id: string) {
    setItems((current) => current.filter((item) => item.id !== id));
  }

  return <div className="space-y-6">
    <Surface className="p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Eyebrow>PERSONAL PRODUCTIVITY</Eyebrow>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">My todo board</h1>
        </div>
        <span className="inline-flex items-center gap-2 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-[10px] font-semibold text-blue-700"><Sparkles size={12} /> Personal workspace</span>
      </div>

      <form onSubmit={addTask} className="mt-5 grid gap-3 rounded-2xl border border-line bg-panel-raised p-4 md:grid-cols-2">
        <label className="grid gap-1 text-[10px] font-medium text-muted md:col-span-2">
          Task title
          <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Prepare loan review notes" />
        </label>
        <label className="grid gap-1 text-[10px] font-medium text-muted md:col-span-2">
          Description
          <Input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Review fees, risk points, and key dates" />
        </label>
        <label className="grid gap-1 text-[10px] font-medium text-muted">
          Assign to
          <Input value={owner} onChange={(event) => setOwner(event.target.value)} placeholder={userName} />
        </label>
        <label className="grid gap-1 text-[10px] font-medium text-muted">
          Due date
          <Input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
        </label>
        <label className="grid gap-1 text-[10px] font-medium text-muted">
          Priority
          <select value={priority} onChange={(event) => setPriority(event.target.value as TodoItem["priority"])} className="min-h-10 rounded-lg border border-line bg-panel px-3 text-xs text-ink">
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </label>
        <div className="flex items-end md:col-span-2">
          <Button type="submit" className="inline-flex items-center gap-2">
            <Plus size={14} />Add task
          </Button>
        </div>
      </form>
    </Surface>

    <div className="grid gap-4 xl:grid-cols-3">
      {statusOrder.map((status) => (
        <Surface key={status} className="min-h-[18rem] p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold">{statusLabels[status]}</h2>
            <span className="rounded-full bg-panel-raised px-2 py-1 text-[10px] text-muted">{grouped[status].length}</span>
          </div>
          <div className="space-y-3">
            {grouped[status].length === 0 && <p className="rounded-xl border border-dashed border-line bg-panel-raised px-3 py-6 text-center text-[11px] text-muted">No tasks here yet.</p>}
            {grouped[status].map((item) => (
              <article key={item.id} className="rounded-xl border border-line bg-panel-raised p-3 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="break-words text-sm font-semibold">{item.title}</p>
                    {item.description && <p className="mt-1 text-[11px] leading-5 text-muted">{item.description}</p>}
                  </div>
                  <button type="button" onClick={() => deleteTask(item.id)} aria-label={`Delete ${item.title}`} className="text-muted hover:text-red-600"><Trash2 size={14} /></button>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] text-muted">
                  <span className={`rounded-full border px-2 py-1 font-medium ${priorityStyles[item.priority]}`}>{item.priority}</span>
                  {item.dueDate && <span className="rounded-full border border-line bg-panel px-2 py-1">Due {new Date(item.dueDate).toLocaleDateString()}</span>}
                </div>

                <div className="mt-3 flex items-center justify-between text-[10px] text-muted">
                  <span>Owner: {item.owner}</span>
                  <span>{new Date(item.createdAt).toLocaleDateString()}</span>
                </div>

                <div className="mt-3 flex items-center justify-between gap-2">
                  <button type="button" disabled={status === "backlog"} onClick={() => moveTask(item.id, -1)} className="rounded-md border border-line bg-panel px-2 py-1 text-[10px] font-medium disabled:cursor-not-allowed disabled:opacity-40">Back</button>
                  <button type="button" disabled={status === "done"} onClick={() => moveTask(item.id, 1)} className="rounded-md border border-line bg-panel px-2 py-1 text-[10px] font-medium disabled:cursor-not-allowed disabled:opacity-40">Next</button>
                </div>
              </article>
            ))}
          </div>
        </Surface>
      ))}
    </div>
  </div>;
}
