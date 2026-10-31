import type { Metadata } from "next";
import { VerifyEmailFlow } from "@/features/auth/auth-form";
export const metadata: Metadata = { title: "Verify email" };
export default async function VerifyEmailPage({ searchParams }: { searchParams: Promise<{ token?: string; verify?: string }> }) {
  const params = await searchParams;
  return <VerifyEmailFlow initialToken={params.token ?? params.verify ?? ""} />;
}
