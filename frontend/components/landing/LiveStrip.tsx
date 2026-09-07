"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Stats = { total_detections: number; with_plate: number; plate_rate_pct: number; total_alerts: number };

function useLiveStats() {
  return useQuery({
    queryKey: ["landing-detections-stats"],
    queryFn: () => api<Stats>("/api/detections/stats"),
    retry: 0,
  });
}

function Counter({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col items-center gap-1 px-4 py-2 text-center">
      <span className="mono text-2xl font-semibold text-text sm:text-3xl">{value}</span>
      <span className="text-xs uppercase tracking-wide text-muted">{label}</span>
    </div>
  );
}

export function LiveStrip() {
  const { data, isError, isSuccess } = useLiveStats();
  const ok = isSuccess && !isError;

  return (
    <section aria-label="Live backend statistics" className="border-y border-border bg-surface">
      <div className="mx-auto flex max-w-6xl flex-col items-center gap-4 px-6 py-8">
        <div className="flex items-center gap-2 text-xs font-medium text-muted">
          <span
            aria-hidden="true"
            className={`h-2 w-2 rounded-full ${ok ? "animate-dot-pulse bg-ok" : "bg-bad"}`}
          />
          <span>
            {ok
              ? "Live from the NETRA backend"
              : "Backend offline — numbers appear when the pipeline is up."}
          </span>
        </div>
        <div className="grid w-full grid-cols-2 divide-x divide-border sm:grid-cols-4">
          <Counter label="Detections" value={ok ? String(data!.total_detections) : "—"} />
          <Counter label="With plate" value={ok ? String(data!.with_plate) : "—"} />
          <Counter label="Plate rate" value={ok ? `${data!.plate_rate_pct}%` : "—"} />
          <Counter label="Alerts" value={ok ? String(data!.total_alerts) : "—"} />
        </div>
      </div>
    </section>
  );
}
