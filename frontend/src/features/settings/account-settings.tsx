"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, LoaderCircle, MonitorSmartphone, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eyebrow, Surface } from "@/components/ui/surface";
import { ApiError } from "@/lib/api/client";
import { api } from "@/lib/api/queries";
import { formatDate } from "@/lib/utils";
import { passwordSchema } from "@/features/auth/schemas";

const profileSchema = z.object({ name: z.string().trim().min(1, "Name is required.").max(80) });
const deleteSchema = z.object({ password: z.string().min(1), confirmation: z.string().refine((value) => value === "DELETE", "Type DELETE to confirm.") });
type ProfileData = z.infer<typeof profileSchema>;
type PasswordData = z.infer<typeof passwordSchema>;
type DeleteInput = z.input<typeof deleteSchema>;
type DeleteData = z.output<typeof deleteSchema>;

function ProfileForm({ initialName }: { initialName: string }) {
  const queryClient = useQueryClient();
  const form = useForm<ProfileData>({ resolver: zodResolver(profileSchema), defaultValues: { name: initialName } });
  const mutation = useMutation({ mutationFn: (values: ProfileData) => api.profile(values.name), onSuccess: ({ user }) => { form.reset({ name: user.name }); queryClient.setQueryData(["me"], user); toast.success("Profile updated."); } });
  return <form className="grid max-w-lg gap-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}><label className="grid gap-2 text-xs font-medium">Display name<Input autoComplete="name" {...form.register("name")} />{form.formState.errors.name && <span className="text-xs text-red-700">{form.formState.errors.name.message}</span>}</label><Button className="w-fit" type="submit" disabled={mutation.isPending}>{mutation.isPending && <LoaderCircle className="animate-spin" size={15} />}Save profile</Button>{mutation.isError && <p role="alert" className="text-xs text-red-700">{mutation.error.message}</p>}</form>;
}

function PasswordForm() {
  const form = useForm<PasswordData>({ resolver: zodResolver(passwordSchema) });
  const mutation = useMutation({ mutationFn: (values: PasswordData) => api.changePassword(values.current_password, values.new_password, values.password_confirmation), onSuccess: () => { form.reset(); toast.success("Password updated. Other sessions were ended."); } });
  return <form className="grid max-w-lg gap-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
    {([["current_password", "Current password", "current-password"], ["new_password", "New password · 12 characters minimum", "new-password"], ["password_confirmation", "Confirm new password", "new-password"]] as const).map(([name, label, autoComplete]) => <label className="grid gap-2 text-xs font-medium" key={name}>{label}<Input type="password" autoComplete={autoComplete} {...form.register(name)} />{form.formState.errors[name] && <span className="text-xs text-red-700">{form.formState.errors[name]?.message}</span>}</label>)}
    <Button className="w-fit" type="submit" disabled={mutation.isPending}>{mutation.isPending && <LoaderCircle className="animate-spin" size={15} />}Change password</Button>{mutation.isError && <p role="alert" className="text-xs text-red-700">{mutation.error.message}</p>}
  </form>;
}

function Sessions() {
  const queryClient = useQueryClient();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions });
  const revoke = useMutation({ mutationFn: api.revokeSession, onSuccess: () => { toast.success("Session ended."); void queryClient.invalidateQueries({ queryKey: ["sessions"] }); } });
  return <div className="space-y-3">{sessions.isLoading && <p className="text-xs text-muted">Loading sessions…</p>}{sessions.isError && <p role="alert" className="text-xs text-red-700">{sessions.error.message}</p>}{sessions.data?.length === 0 && <p className="text-xs text-muted">No active sessions.</p>}{sessions.data?.map((session) => <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-slate-50 p-3" key={session.id}><div className="flex items-center gap-3"><MonitorSmartphone className="text-teal" size={17} /><div><p className="text-xs font-medium">{session.current ? "This session" : "Signed-in session"}</p><p className="mt-1 text-[10px] text-muted">Created {formatDate(session.created_at)} · Expires {formatDate(session.expires_at)}</p></div></div>{!session.current && <Button size="sm" variant="secondary" onClick={() => revoke.mutate(session.id)} disabled={revoke.isPending}>End session</Button>}</div>)}</div>;
}

export function AccountSettings() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = useQuery({ queryKey: ["me"], queryFn: api.me });
  const [exportHref, setExportHref] = useState<string | null>(null);
  const [archiveHref, setArchiveHref] = useState<string | null>(null);
  const exportData = useMutation({
    mutationFn: api.exportData,
    onSuccess: (data) => { if (exportHref) URL.revokeObjectURL(exportHref); const href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })); setExportHref(href); toast.success("Your account export is ready."); },
    onError: (error) => toast.error(error.message),
  });
  const exportArchive = useMutation({
    mutationFn: api.exportArchive,
    onSuccess: (data) => { if (archiveHref) URL.revokeObjectURL(archiveHref); setArchiveHref(URL.createObjectURL(data)); toast.success("Your full account archive is ready."); },
    onError: (error) => toast.error(error.message),
  });
  useEffect(() => () => { if (exportHref) URL.revokeObjectURL(exportHref); if (archiveHref) URL.revokeObjectURL(archiveHref); }, [exportHref, archiveHref]);

  const deletionForm = useForm<DeleteInput, unknown, DeleteData>({ resolver: zodResolver(deleteSchema) });
  const deleteAccount = useMutation({
    mutationFn: (values: DeleteData) => api.deleteAccount(values.password),
    onSuccess: () => { queryClient.clear(); toast.success("Your account and local analyses were deleted."); router.replace("/"); },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not delete the account."),
  });

  return <div className="space-y-6">
    <div><Eyebrow>ACCOUNT SETTINGS</Eyebrow><h1 className="mt-2 text-3xl font-semibold tracking-[-.04em]">Profile & security</h1><p className="mt-2 text-sm text-muted">Manage your account details, active sessions, and stored data.</p></div>
    <Surface className="p-5 sm:p-6"><div className="mb-5 flex items-center gap-2"><ShieldCheck size={17} className="text-teal" /><h2 className="text-sm font-semibold">Profile</h2></div>{user.data && <><p className="mb-4 text-xs text-muted">{user.data.email} · {user.data.email_verified ? "Email verified" : "Email not verified"}</p><ProfileForm key={user.data.id} initialName={user.data.name} /></>}</Surface>
    <Surface className="p-5 sm:p-6"><Eyebrow>CHANGE PASSWORD</Eyebrow><p className="mb-5 mt-1 text-xs text-muted">Changing your password ends other active sessions.</p><PasswordForm /></Surface>
    <Surface className="p-5 sm:p-6"><Eyebrow>ACTIVE SESSIONS</Eyebrow><p className="mb-5 mt-1 text-xs text-muted">Review and revoke sessions associated with your account.</p><Sessions /></Surface>
    <Surface className="p-5 sm:p-6"><Eyebrow>YOUR DATA</Eyebrow><p className="mt-1 max-w-2xl text-xs leading-5 text-muted">Export your account profile, analyses, and retained originals, or permanently delete your account and its local document records.</p><div className="mt-4 flex flex-wrap items-center gap-2"><Button variant="secondary" onClick={() => exportData.mutate()} disabled={exportData.isPending}>{exportData.isPending ? <LoaderCircle className="animate-spin" size={14} /> : <Download size={14} />}Prepare JSON export</Button>{exportHref && <a className="rounded-lg border border-teal/20 bg-teal/10 px-3 py-2 text-xs font-semibold text-teal" href={exportHref} download="bankease-account-export.json">Download JSON</a>}<Button variant="secondary" onClick={() => exportArchive.mutate()} disabled={exportArchive.isPending}>{exportArchive.isPending ? <LoaderCircle className="animate-spin" size={14} /> : <Download size={14} />}Prepare full archive</Button>{archiveHref && <a className="rounded-lg border border-teal/20 bg-teal/10 px-3 py-2 text-xs font-semibold text-teal" href={archiveHref} download="bankease-account-export.zip">Download ZIP</a>}</div><p className="mt-2 text-[10px] leading-4 text-muted">The ZIP includes account data, a file manifest, and available originals. Uncompressed export size is limited to 100 MB.</p></Surface>
    <Surface className="border-red-200 p-5 sm:p-6"><div className="flex items-center gap-2 text-red-700"><Trash2 size={16} /><Eyebrow className="text-red-700">DELETE ACCOUNT</Eyebrow></div><p className="mt-2 max-w-2xl text-xs leading-5 text-muted">This permanently removes the account and the local document analyses associated with it.</p><form className="mt-4 grid max-w-lg gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end" onSubmit={deletionForm.handleSubmit((values) => deleteAccount.mutate(values))}><label className="grid gap-2 text-xs">Confirm password<Input type="password" autoComplete="current-password" {...deletionForm.register("password")} /></label><label className="grid gap-2 text-xs">Type DELETE<Input autoComplete="off" {...deletionForm.register("confirmation")} />{deletionForm.formState.errors.confirmation && <span className="text-[10px] text-red-700">{deletionForm.formState.errors.confirmation.message}</span>}</label><Button variant="danger" type="submit" disabled={deleteAccount.isPending}>{deleteAccount.isPending && <LoaderCircle className="animate-spin" size={14} />}Delete account</Button></form>
    </Surface>
    <p className="text-[11px] leading-5 text-muted">Development privacy note: local source files and extracted-text records are not encrypted at rest, and outbound account email is not configured.</p>
  </div>;
}
