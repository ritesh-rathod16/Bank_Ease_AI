"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, LoaderCircle, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState, type ComponentProps, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { ApiError, apiFetch, jsonRequest } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eyebrow } from "@/components/ui/surface";
import { emailSchema, loginSchema, registerSchema, resetSchema } from "@/features/auth/schemas";
import type { User } from "@/types/api";

type Mode = "login" | "register" | "forgot" | "reset";

function localLink(source: string | null | undefined, key: "verify" | "reset") {
  if (!source) return null;
  try {
    const url = new URL(source, window.location.origin);
    const token = url.searchParams.get(key);
    return token ? `${key === "verify" ? "/verify-email" : "/reset-password"}?token=${encodeURIComponent(token)}` : null;
  } catch { return null; }
}

function Field({ label, error, className, ...props }: { label: string; error?: string; className?: string } & ComponentProps<typeof Input>) {
  return <label className="grid gap-1.5 text-xs font-medium text-[#172033]">{label}<Input {...props} className={`bg-[#F8FAFC] border-[#DCE3EA] text-[#172033] placeholder:text-[#94A3B8] ${className ?? ""}`} aria-invalid={Boolean(error)} />{error && <span className="text-xs text-red-600">{error}</span>}</label>;
}

function Frame({ mode, children, note }: { mode: Mode; children: ReactNode; note?: ReactNode }) {
  const title: Record<Mode, string> = { login: "Sign in to your workspace", register: "Create your account", forgot: "Recover your account", reset: "Choose a new password" };
  const description: Record<Mode, string> = {
    login: "Your loan documents and analysis are private to your account.",
    register: "Use a verified email address to activate your private workspace.",
    forgot: "If an account matches, reset instructions will be provided.",
    reset: "Use a new password with at least 12 characters.",
  };
  return <main className="flex min-h-screen items-center justify-center px-4 py-10 sm:px-6">
    <div className="grid w-full max-w-[1020px] overflow-hidden rounded-2xl border border-[#DCE3EA] bg-[#FFFFFF] shadow-[0_30px_80px_rgba(15,23,42,0.12)] md:grid-cols-[.95fr_1.05fr]">
      <section className="hidden flex-col justify-between bg-[#102820] p-10 text-white md:flex">
        <Link className="flex items-center gap-3 text-white" href="/"><span className="grid size-10 place-items-center rounded-xl border border-[#9AE6B4]/30 bg-[#163c32] text-[#B6F7D7]"><ShieldCheck size={21} /></span><span className="font-semibold tracking-tight">BankEase <span className="text-[#9AE6B4]">AI</span></span></Link>
        <div className="max-w-sm"><Eyebrow className="text-[#A7F3D0]">DOCUMENT-GROUNDED LOAN INSIGHTS</Eyebrow><h1 className="mt-4 text-4xl font-semibold leading-tight tracking-[-.04em] text-white">Make the fine print easier to understand.</h1><p className="mt-4 text-sm leading-6 text-[#CFE6DF]">Review extracted terms, check repayment math, and find source passages in your loan documents.</p></div>
        <p className="text-xs leading-5 text-[#CFE6DF]">Informational and educational use only. Always verify important details against your original agreement.</p>
      </section>
      <section className="bg-[#FFFFFF] p-6 sm:p-10">
        <div className="mb-8 md:hidden"><Link className="font-semibold tracking-tight text-[#172033]" href="/">BankEase <span className="text-[#4F46E5]">AI</span></Link></div>
        <Eyebrow className="text-[#4F46E5]">ACCOUNT ACCESS</Eyebrow><h2 className="mt-2 text-2xl font-semibold tracking-tight text-[#172033]">{title[mode]}</h2><p className="mt-2 text-sm text-[#64748B]">{description[mode]}</p>
        <div className="mt-7">{children}</div>
        {note && <div className="mt-5 rounded-lg border border-[#D7EDE7] bg-[#F3F8F6] p-3 text-xs leading-5 text-[#2B4D43]">{note}</div>}
        <p className="mt-8 border-t border-[#E2E8F0] pt-4 text-[11px] leading-5 text-[#64748B]">Passwords are stored using a one-way password hash. Sessions are stored server-side and use HTTP-only cookies.</p>
      </section>
    </div>
  </main>;
}

function FormMessage({ children, kind = "info" }: { children: ReactNode; kind?: "info" | "error" | "success" }) {
  return <div role="status" aria-live="polite" className={`rounded-lg border px-3 py-2.5 text-xs leading-5 ${kind === "error" ? "border-red-200 bg-red-50 text-red-700" : kind === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-[#DCE3EA] bg-[#F8FAFC] text-[#334155]"}`}>{children}</div>;
}

function Submit({ busy, children }: { busy: boolean; children: ReactNode }) {
  return <Button className="mt-2 w-full bg-[#4F46E5] text-white hover:bg-[#4338CA] active:bg-[#3730A3] focus-visible:ring-4 focus-visible:ring-[#C7D2FE] focus-visible:ring-offset-2 focus-visible:ring-offset-white disabled:opacity-50" type="submit" disabled={busy}>{busy ? <LoaderCircle className="animate-spin" size={16} /> : <ArrowRight size={16} />}{children}</Button>;
}

function LoginForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const form = useForm<z.infer<typeof loginSchema>>({ resolver: zodResolver(loginSchema) });
  const mutation = useMutation({
    mutationFn: (values: z.infer<typeof loginSchema>) => apiFetch<{ user: User }>("/api/v1/auth/login", jsonRequest("POST", values)),
    onSuccess: async ({ user }) => { queryClient.setQueryData(["me"], user); await queryClient.invalidateQueries({ queryKey: ["documents"] }); router.push("/dashboard"); },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not sign in. Try again."),
  });
  return <form className="grid gap-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
    <Field label="Email address" type="email" autoComplete="email" placeholder="you@example.com" {...form.register("email")} error={form.formState.errors.email?.message} />
    <Field label="Password" type="password" autoComplete="current-password" {...form.register("password")} error={form.formState.errors.password?.message} />
    {mutation.isError && <FormMessage kind="error">{mutation.error.message}</FormMessage>}
    <Submit busy={mutation.isPending}>Sign in</Submit>
    <div className="flex flex-wrap justify-between gap-3 text-xs"><Link className="text-[#4F46E5] hover:text-[#4338CA] hover:underline" href="/forgot-password">Forgot password?</Link><Link className="text-[#64748B] hover:text-[#172033] hover:underline" href="/register">Create an account</Link></div>
    <Link className="text-xs text-[#64748B] hover:text-[#4F46E5] hover:underline" href="/verify-email">Resend email verification</Link>
  </form>;
}

function RegisterForm() {
  const [verificationPath, setVerificationPath] = useState<string | null>(null);
  const form = useForm<z.infer<typeof registerSchema>>({ resolver: zodResolver(registerSchema) });
  const mutation = useMutation({
    mutationFn: (values: z.infer<typeof registerSchema>) => apiFetch<{ message: string; development_verification_url?: string | null }>("/api/v1/auth/register", jsonRequest("POST", values)),
    onSuccess: (payload) => { setVerificationPath(localLink(payload.development_verification_url, "verify")); toast.success(payload.message); },
  });
  return <form className="grid gap-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
    <Field label="Your name" autoComplete="name" {...form.register("name")} error={form.formState.errors.name?.message} />
    <Field label="Email address" type="email" autoComplete="email" {...form.register("email")} error={form.formState.errors.email?.message} />
    <Field label="Password · at least 12 characters" type="password" autoComplete="new-password" {...form.register("password")} error={form.formState.errors.password?.message} />
    <Field label="Confirm password" type="password" autoComplete="new-password" {...form.register("password_confirmation")} error={form.formState.errors.password_confirmation?.message} />
    {mutation.isError && <FormMessage kind="error">{mutation.error.message}</FormMessage>}
    {mutation.isSuccess && <FormMessage kind="success">{mutation.data.message}{verificationPath && <> <Link className="font-semibold underline" href={verificationPath}>Open the local verification link</Link></>}</FormMessage>}
    <Submit busy={mutation.isPending}>Create account</Submit>
    <p className="text-xs text-[#64748B]">Email delivery is not configured. In local development, the verification link appears here.</p>
    <Link className="text-xs text-[#4F46E5] hover:text-[#4338CA] hover:underline" href="/login">Already registered? Sign in</Link>
  </form>;
}

function ForgotForm() {
  const [resetPath, setResetPath] = useState<string | null>(null);
  const form = useForm<z.infer<typeof emailSchema>>({ resolver: zodResolver(emailSchema) });
  const mutation = useMutation({
    mutationFn: (values: z.infer<typeof emailSchema>) => apiFetch<{ message: string; development_reset_url?: string | null }>("/api/v1/auth/forgot-password", jsonRequest("POST", values)),
    onSuccess: (payload) => { setResetPath(localLink(payload.development_reset_url, "reset")); toast.success(payload.message); },
  });
  return <form className="grid gap-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
    <Field label="Email address" type="email" autoComplete="email" {...form.register("email")} error={form.formState.errors.email?.message} />
    {mutation.isError && <FormMessage kind="error">{mutation.error.message}</FormMessage>}
    {mutation.isSuccess && <FormMessage kind="success">{mutation.data.message}{resetPath && <> <Link className="font-semibold underline" href={resetPath}>Open the local reset link</Link></>}</FormMessage>}
    <Submit busy={mutation.isPending}>Request reset</Submit>
    <Link className="text-xs text-[#4F46E5] hover:text-[#4338CA] hover:underline" href="/login">Back to sign in</Link>
  </form>;
}

function ResetForm({ token }: { token: string }) {
  const router = useRouter();
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.toString());
  }, []);
  const form = useForm<z.infer<typeof resetSchema>>({ resolver: zodResolver(resetSchema) });
  const mutation = useMutation({
    mutationFn: (values: z.infer<typeof resetSchema>) => apiFetch<{ message: string }>("/api/v1/auth/reset-password", jsonRequest("POST", { ...values, token })),
    onSuccess: (result) => { toast.success(result.message); router.push("/login?reset=complete"); },
  });
  return <form className="grid gap-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
    {!token && <FormMessage kind="error">This reset link is missing its token. Request a new one.</FormMessage>}
    <Field label="New password · at least 12 characters" type="password" autoComplete="new-password" {...form.register("password")} error={form.formState.errors.password?.message} />
    <Field label="Confirm new password" type="password" autoComplete="new-password" {...form.register("password_confirmation")} error={form.formState.errors.password_confirmation?.message} />
    {mutation.isError && <FormMessage kind="error">{mutation.error.message}</FormMessage>}
    <Submit busy={mutation.isPending || !token}>Update password</Submit>
  </form>;
}

export function AuthForm({ mode, initialToken = "" }: { mode: Mode; initialToken?: string }) {
  const note = mode === "register" || mode === "forgot" ? "This local prototype has no outbound email provider. A development-only link is shown after the request." : undefined;
  return <Frame mode={mode} note={note}>
    {mode === "login" && <LoginForm />}
    {mode === "register" && <RegisterForm />}
    {mode === "forgot" && <ForgotForm />}
    {mode === "reset" && <ResetForm token={initialToken} />}
  </Frame>;
}

export function VerifyEmailFlow({ initialToken = "" }: { initialToken?: string }) {
  const lastToken = useRef<string | null>(null);
  const form = useForm<z.infer<typeof emailSchema>>({ resolver: zodResolver(emailSchema) });
  const verification = useMutation({
    mutationFn: (token: string) => apiFetch<{ message: string }>("/api/v1/auth/verify-email", jsonRequest("POST", { token })),
  });
  const verifyToken = verification.mutate;

  useEffect(() => {
    if (!initialToken) return;
    if (lastToken.current !== initialToken) {
      lastToken.current = initialToken;
      verifyToken(initialToken);
    }
    const url = new URL(window.location.href);
    url.searchParams.delete("token");
    url.searchParams.delete("verify");
    window.history.replaceState({}, "", url.toString());
  }, [initialToken, verifyToken]);

  const resend = useMutation({
    mutationFn: (values: z.infer<typeof emailSchema>) => apiFetch<{ message: string; development_verification_url?: string | null }>("/api/v1/auth/resend-verification", jsonRequest("POST", values)),
  });

  return <Frame mode="login" note="Verification links are generated locally for this development app. Production email delivery has not been configured.">
    <div className="grid gap-4">
      <FormMessage kind={verification.isError ? "error" : verification.isSuccess || resend.isSuccess ? "success" : "info"}>{verification.isPending ? "Checking the verification token…" : verification.error?.message ?? verification.data?.message ?? resend.data?.message ?? "Open the verification link from your account registration."}</FormMessage>
      <form className="grid gap-4" onSubmit={form.handleSubmit((values) => resend.mutate(values))}>
        <Field label="Email address" type="email" autoComplete="email" {...form.register("email")} error={form.formState.errors.email?.message} />
        {resend.isError && <FormMessage kind="error">{resend.error.message}</FormMessage>}
        {resend.isSuccess && resend.data.development_verification_url && <FormMessage kind="success"><Link className="underline" href={localLink(resend.data.development_verification_url, "verify") ?? "/verify-email"}>Open the local verification link</Link></FormMessage>}
        <Submit busy={resend.isPending || verification.isPending}>Resend verification</Submit>
      </form>
      <Link className="text-xs text-[#4F46E5] hover:text-[#4338CA] hover:underline" href="/login">Back to sign in</Link>
    </div>
  </Frame>;
}
