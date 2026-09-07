"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [qc] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchInterval: 4000, retry: 1, staleTime: 2000 } } }));
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
