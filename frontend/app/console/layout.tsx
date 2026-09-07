"use client";
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { LiveProvider } from "@/lib/live";
import { Shell } from "@/components/Shell";
import { Unreachable } from "@/components/Unreachable";

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const failsRef = useRef(0);
  const [unreachable, setUnreachable] = useState(false);

  useQuery({
    queryKey: ["ping"],
    queryFn: async () => {
      try {
        const r = await api("/api/pipeline/status");
        failsRef.current = 0;
        setUnreachable(false);
        return r;
      } catch (e) {
        if (e instanceof ApiError && e.status === 0) {
          failsRef.current += 1;
          if (failsRef.current >= 2) setUnreachable(true);
        }
        throw e;
      }
    },
    refetchInterval: 5000,
    retry: false,
  });

  if (unreachable) return <Unreachable />;

  return (
    <LiveProvider>
      <Shell>{children}</Shell>
    </LiveProvider>
  );
}
