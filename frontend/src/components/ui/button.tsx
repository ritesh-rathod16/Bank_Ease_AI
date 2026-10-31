"use client";

import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const buttonStyles = cva("inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/60 disabled:pointer-events-none disabled:opacity-45", {
  variants: {
    variant: {
      primary: "bg-teal text-white hover:bg-teal-strong",
      secondary: "border border-line bg-panel-raised text-ink hover:bg-slate-200",
      ghost: "text-muted hover:bg-panel-raised hover:text-ink",
      danger: "border border-red-200 bg-red-50 text-red-800 hover:bg-red-100",
    },
    size: { default: "", sm: "min-h-8 px-3 text-xs", icon: "size-9 min-h-0 p-0" },
  },
  defaultVariants: { variant: "primary", size: "default" },
});

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonStyles>;
export function Button({ className, variant, size, type = "button", ...props }: ButtonProps) {
  return <button className={cn(buttonStyles({ variant, size }), className)} type={type} {...props} />;
}
