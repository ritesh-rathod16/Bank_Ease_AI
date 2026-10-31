import type { Metadata } from "next";
import type { ReactNode } from "react";
import Script from "next/script";
import { Providers } from "@/providers/query-provider";
import { ThemeProvider } from "@/providers/theme-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "BankEase AI — Loan document clarity", template: "%s · BankEase AI" },
  description: "Understand the terms, repayment and clauses in your loan documents with document-grounded analysis.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return <html lang="en" data-scroll-behavior="smooth" suppressHydrationWarning>
    <body>
      <Script id="bankease-theme-init" strategy="beforeInteractive">{`try { const saved = localStorage.getItem("bankease-theme"); const theme = saved === "dark" || saved === "light" ? saved : window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"; document.documentElement.dataset.theme = theme; } catch {}`}</Script>
      <ThemeProvider><Providers>{children}</Providers></ThemeProvider>
    </body>
  </html>;
}
