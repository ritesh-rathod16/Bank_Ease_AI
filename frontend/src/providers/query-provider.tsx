"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Toaster } from "sonner";
import { useTheme } from "@/providers/theme-provider";

export function Providers({ children }: Readonly<{ children: ReactNode }>) {
  const { theme } = useTheme();
  const [client] = useState(() => new QueryClient({
    defaultOptions: { queries: { staleTime: 20_000, refetchOnWindowFocus: false, retry: 1 } },
  }));
  return <QueryClientProvider client={client}>{children}<Toaster theme={theme} position="top-right" richColors /></QueryClientProvider>;
}
