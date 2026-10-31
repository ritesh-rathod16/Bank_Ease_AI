"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { Check, ClipboardCheck, LoaderCircle, MessageSquareText, UserRoundCog } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { api } from "@/lib/api/queries";
import type { ReviewTask, UserRole } from "@/types/api";
import { formatDate, humanize } from "@/lib/utils";

const assignSchema = z.object({ email: z.string().trim().email("Enter the verified staff member's email.") });
const roleSchema = z.object({ email: z.string().trim().email(), role: z.enum(["customer", "document_reviewer", "bank_employee", "loan_officer", "branch_manager", "auditor"]) });
const responseSchema = z.object({ message: z.string().trim().min(1, "Add a short response.").max(1000) });
type RoleForm = z.infer<typeof roleSchema>;

function AssignForm({ task }: { task: ReviewTask }) {
  const queryClient = useQueryClient();
  const form = useForm<z.infer<typeof assignSchema>>({ resolver: zodResolver(assignSchema) });
  const assign = useMutation({ mutationFn: (values: z.infer<typeof assignSchema>) => api.assignReview(task.id, values.email),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["reviews"] }); form.reset(); toast.success("Review assigned."); },
    onError: (error) => toast.error(error.message) });
  return <form className="mt-4 flex flex-wrap items-end gap-2" onSubmit={form.handleSubmit((values) => assign.mutate(values))}>
    <label className="grid min-w-56 flex-1 gap-1 text-[10px] text-muted">Assign to verified staff<Input type="email" placeholder="reviewer@bank.example" {...form.register("email")} />{form.formState.errors.email && <span className="text-red-700">{form.formState.errors.email.message}</span>}</label>
    <Button size="sm" disabled={assign.isPending}>{assign.isPending && <LoaderCircle className="animate-spin" size={13} />}Assign</Button>
  </form>;
}

function TransactionSnapshot({ snapshot }: { snapshot: NonNullable<ReviewTask["snapshot"]> }) {
  const source = snapshot.source_values ?? {};
  const effective = snapshot.effective_values ?? {};
  const money = (value: unknown) => typeof value === "number" ? `${value} minor units` : String(value ?? "—");
  const value = (record: Record<string, unknown>, key: string) => key.endsWith("_minor") ? money(record[key]) : String(record[key] ?? "—");
  const fields = ["date", "description", "amount_minor", "debit_minor", "credit_minor", "balance_minor", "currency"];
  return <div className="mt-4 rounded-lg border border-line bg-white p-4">
    <p className="text-[10px] font-semibold uppercase tracking-wider text-teal">Statement row {snapshot.row_number}{snapshot.source_sheet ? ` · ${snapshot.source_sheet}` : ""} · source evidence</p>
    <p className="mt-2 text-[10px] text-muted">{snapshot.page ? `Page ${snapshot.page}` : snapshot.source_sheet ? `${snapshot.source_sheet}, row ${snapshot.row_number}` : "CSV row"} · correction revision {snapshot.correction_revision ?? 0}</p>
    <div className="mt-3 grid gap-3 sm:grid-cols-2"><div><p className="text-[10px] font-medium text-muted">Imported values</p><dl className="mt-1 space-y-1">{fields.map((key) => <div key={key} className="flex justify-between gap-3 text-xs"><dt className="text-muted">{humanize(key)}</dt><dd className="text-right tabular-nums">{value(source, key)}</dd></div>)}</dl></div><div><p className="text-[10px] font-medium text-muted">Effective values</p><dl className="mt-1 space-y-1">{fields.map((key) => <div key={key} className="flex justify-between gap-3 text-xs"><dt className="text-muted">{humanize(key)}</dt><dd className="text-right tabular-nums">{value(effective, key)}</dd></div>)}</dl></div></div>
    {source.source_values !== undefined && <p className="mt-3 break-words text-[10px] leading-4 text-muted">Original cells: {JSON.stringify(source.source_values) ?? ""}</p>}
    {source.evidence !== undefined && <p className="mt-2 border-l border-teal/40 pl-2 text-xs leading-5 text-muted">{String(source.evidence)}</p>}
    {snapshot.user_correction && <p className="mt-2 text-[10px] text-teal">Owner correction reason: {snapshot.user_correction.reason ?? "Not provided"}</p>}
  </div>;
}

function RoleManager() {
  const queryClient = useQueryClient();
  const users = useQuery({ queryKey: ["staff-users"], queryFn: api.staffUsers });
  const form = useForm<RoleForm>({ resolver: zodResolver(roleSchema), defaultValues: { role: "document_reviewer" } });
  const change = useMutation({ mutationFn: (values: RoleForm) => api.changeRole(values.email, values.role),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["staff-users"] }); form.reset({ email: "", role: "document_reviewer" }); toast.success("Account role updated."); },
    onError: (error) => toast.error(error.message) });
  return <Surface className="p-5 sm:p-6"><div className="flex items-center gap-2"><UserRoundCog className="text-teal" size={17} /><Eyebrow>STAFF ACCESS</Eyebrow></div>
    <p className="mt-2 text-xs leading-5 text-muted">Roles are assigned by a configured administrator. Staff accounts see customer evidence only for tasks assigned to them.</p>
    <form className="mt-4 grid gap-3 sm:grid-cols-[1fr_220px_auto] sm:items-end" onSubmit={form.handleSubmit((values) => change.mutate(values))}>
      <label className="grid gap-1 text-[10px] text-muted">Account email<Input type="email" {...form.register("email")} />{form.formState.errors.email && <span className="text-red-700">{form.formState.errors.email.message}</span>}</label>
      <label className="grid gap-1 text-[10px] text-muted">Role<select className="h-10 rounded-lg border border-line bg-white px-3 text-xs text-ink" {...form.register("role")}>
        {(["customer", "document_reviewer", "bank_employee", "loan_officer", "branch_manager", "auditor"] as UserRole[]).map((role) => <option key={role} value={role}>{humanize(role)}</option>)}
      </select></label><Button disabled={change.isPending}>{change.isPending && <LoaderCircle className="animate-spin" size={14} />}Update role</Button>
    </form>
    {users.data && <div className="mt-4 flex flex-wrap gap-2">{users.data.users.filter((item) => item.role !== "customer").map((item) => <span key={item.id} className="rounded-full border border-line px-3 py-1 text-[10px] text-muted">{item.email} · {humanize(item.role)}</span>)}</div>}
    {users.isError && <p role="alert" className="mt-3 text-xs text-red-700">{users.error.message}</p>}
  </Surface>;
}

function ReviewCard({ task, role }: { task: ReviewTask; role: UserRole }) {
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");
  const responseForm = useForm<z.infer<typeof responseSchema>>({ resolver: zodResolver(responseSchema) });
  const action = useMutation({
    mutationFn: (input: { kind: "approve" | "changes" | "reject" | "respond"; value?: string }) => {
      if (input.kind === "respond") return api.respondReview(task.id, input.value ?? "");
      const decision = input.kind === "approve" ? "approved" : input.kind === "reject" ? "rejected" : "needs_owner_action";
      const defaultComment = input.kind === "changes" ? "Please check the value against the source evidence." : input.kind === "reject" ? "Unable to confirm from the provided evidence." : "";
      return api.decideReview(task.id, decision, input.value ?? defaultComment);
    },
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["reviews"] }); toast.success("Review updated."); },
    onError: (error) => toast.error(error.message),
  });
  const staff = role !== "customer" && role !== "system_admin";
  const admin = role === "system_admin";
  return <Surface className="p-5 sm:p-6">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-semibold">{humanize(task.field_id)}</h2><span className="rounded-full bg-panel-raised px-2 py-1 text-[10px] text-muted">{humanize(task.status)}</span></div><p className="mt-1 text-[11px] text-muted">{task.snapshot?.filename ?? `Document ${task.document_id.slice(0, 8)}`} · Requested {formatDate(task.created_at)}</p></div><div className="flex items-center gap-3">{task.assigned_to && admin && <span className="text-[10px] text-muted">Assigned · {task.assigned_to.slice(0, 8)}</span>}<Link href={`/dashboard/assistant?document_id=${task.document_id}`} className="inline-flex items-center gap-1 text-[10px] font-semibold text-teal hover:underline"><MessageSquareText size={13} />Ask AI</Link></div></div>
    {task.reason && <p className="mt-3 text-xs leading-5 text-muted">Request: {task.reason}</p>}
    {task.snapshot?.kind === "statement_transaction" ? <TransactionSnapshot snapshot={task.snapshot} /> : task.snapshot && <div className="mt-4 rounded-lg border border-line bg-white p-4"><p className="text-[10px] font-semibold uppercase tracking-wider text-teal">Submitted source evidence</p><p className="mt-2 text-xs">Extracted: <span className="font-semibold tabular-nums">{String(task.snapshot.value ?? "Not found")}</span>{task.snapshot.raw ? ` · Source: ${task.snapshot.raw}` : ""}</p>{task.snapshot.user_correction && <p className="mt-2 text-xs text-teal">Owner correction saved: {String(task.snapshot.effective_value ?? task.snapshot.user_correction.value)}{task.snapshot.user_correction.raw ? ` · ${task.snapshot.user_correction.raw}` : ""}</p>}<p className="mt-2 text-xs leading-5 text-muted">{task.snapshot.evidence || "No supporting passage was extracted."}</p><p className="mt-2 text-[10px] text-muted">{task.snapshot.page ? `Page ${task.snapshot.page}` : "Page unavailable"} · Extraction status: {humanize(task.snapshot.status ?? "unknown")}</p></div>}
    {task.history?.length > 0 && <details className="mt-3"><summary className="cursor-pointer text-[10px] text-muted">Activity ({task.history.length})</summary><ol className="mt-2 space-y-2">{task.history.map((event, index) => <li key={`${event.created_at}-${index}`} className="border-l border-line pl-3 text-[10px] leading-4 text-muted">{humanize(event.action)} · {formatDate(event.created_at)}{event.comment && <p className="mt-1">{event.comment}</p>}</li>)}</ol></details>}
    {admin && ["submitted", "assigned", "owner_responded"].includes(task.status) && <AssignForm task={task} />}
    {staff && task.status === "assigned" && <div className="mt-4 space-y-2"><label className="grid gap-1 text-[10px] text-muted">Review note (optional)<Textarea maxLength={1000} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Add a concise explanation for the customer." /></label><div className="flex flex-wrap gap-2"><Button size="sm" onClick={() => action.mutate({ kind: "approve", value: comment })} disabled={action.isPending}><Check size={13} />Evidence reviewed</Button><Button size="sm" variant="secondary" onClick={() => action.mutate({ kind: "changes", value: comment })} disabled={action.isPending}>Ask owner to review</Button><Button size="sm" variant="ghost" onClick={() => action.mutate({ kind: "reject", value: comment })} disabled={action.isPending}>Unable to confirm</Button></div></div>}
    {!admin && !staff && task.status === "needs_owner_action" && <form className="mt-4 grid gap-2 sm:grid-cols-[1fr_auto] sm:items-end" onSubmit={responseForm.handleSubmit(({ message }) => action.mutate({ kind: "respond", value: message }))}><label className="grid gap-1 text-[10px] text-muted">Reply to the reviewer<Textarea maxLength={1000} {...responseForm.register("message")} />{responseForm.formState.errors.message && <span className="text-red-700">{responseForm.formState.errors.message.message}</span>}</label><Button size="sm" disabled={action.isPending}>Send response</Button></form>}
  </Surface>;
}

export function ReviewQueue() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const reviews = useQuery({ queryKey: ["reviews"], queryFn: () => api.reviews(), enabled: Boolean(me.data) });
  const role = me.data?.role ?? "customer";
  const isAdmin = role === "system_admin";
  return <div className="space-y-6">
    <section><Eyebrow>BANKEASE AI · REVIEW WORKSPACE</Eyebrow><h1 className="mt-2 text-3xl font-semibold tracking-tight">{isAdmin ? "Assignment desk" : role === "customer" ? "My review requests" : "Assigned reviews"}</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-muted">Review work is limited to submitted tasks. Assigned staff see the selected field and cited passage; customer documents remain private.</p></section>
    {isAdmin && <RoleManager />}
    <div className="flex items-center gap-2"><ClipboardCheck size={16} className="text-teal" /><Eyebrow>{isAdmin ? "REVIEW REQUESTS" : role === "customer" ? "MY REVIEW REQUESTS" : "TASKS ASSIGNED TO ME"}</Eyebrow></div>
    {reviews.isLoading && <div className="flex items-center gap-2 py-10 text-xs text-muted"><LoaderCircle className="animate-spin" size={15} />Loading review tasks…</div>}
    {reviews.isError && <Surface className="p-5"><p role="alert" className="text-xs text-red-700">{reviews.error.message}</p></Surface>}
    {reviews.data?.reviews.length === 0 && <Surface className="p-8 text-center"><p className="text-sm font-semibold">No review tasks yet</p><p className="mt-2 text-xs text-muted">When a customer requests a check on an extracted field, it will appear here.</p></Surface>}
    {reviews.data?.reviews.map((task) => <ReviewCard key={task.id} task={task} role={role} />)}
  </div>;
}
