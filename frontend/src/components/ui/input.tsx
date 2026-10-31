import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const base = "w-full rounded-lg border border-line bg-white px-3 text-sm text-ink placeholder:text-slate-400 outline-none transition focus:border-teal/70 focus:ring-2 focus:ring-teal/15 disabled:opacity-50";
export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(base, "h-10", className)} {...props} />;
}
export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(base, "min-h-24 resize-y py-3", className)} {...props} />;
}
