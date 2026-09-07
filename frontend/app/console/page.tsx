"use client";
import { useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Square, Gauge, MoonStar } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useLive } from "@/lib/live";
import type { Camera, Alert, DetectionStats } from "@/lib/types";
import { mergeHealth } from "@/lib/health";
import { KpiStat } from "@/components/KpiStat";
import { AlertFeed } from "@/components/AlertFeed";
import { HealthStrip } from "@/components/HealthStrip";
import { EmptyState } from "@/components/EmptyState";
import { Gate } from "@/components/Gate";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";

const MiniMap = dynamic(() => import("@/components/MiniMap"), {
  ssr: false,
  loading: () => <Skeleton className="h-64 w-full rounded-card" />,
});

function PipelineControls({ cameras }: { cameras: Camera[] }) {
  const qc = useQueryClient();
  const { status } = useLive();
  const [selected, setSelected] = useState<string[]>([]);

  const start = useMutation({
    mutationFn: () =>
      api(`/api/pipeline/start?cameras=${encodeURIComponent(selected.join(","))}`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Pipeline starting");
      qc.invalidateQueries({ queryKey: ["pipeline"] });
    },
    onError: (e: unknown) => {
      if (e instanceof ApiError) toast.error(e.message);
    },
  });
  const stop = useMutation({
    mutationFn: () => api(`/api/pipeline/stop`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Pipeline stopping");
      qc.invalidateQueries({ queryKey: ["pipeline"] });
    },
    onError: (e: unknown) => {
      if (e instanceof ApiError) toast.error(e.message);
    },
  });

  const escalated = status?.scheduling.escalated ?? [];
  const dark = status?.dark_cameras ?? [];

  return (
    <div className="rounded-card border border-border bg-surface p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Gate min="admin" reason="Sign in as admin to start the pipeline">
          <Button
            size="sm"
            onClick={() => start.mutate()}
            disabled={selected.length === 0 || start.isPending}
          >
            <Play className="size-4" />
            Start pipeline
          </Button>
        </Gate>
        <Gate min="admin" reason="Sign in as admin to stop the pipeline">
          <Button size="sm" variant="secondary" onClick={() => stop.mutate()} disabled={stop.isPending}>
            <Square className="size-4" />
            Stop
          </Button>
        </Gate>
        {escalated.length > 0 && (
          <span className="mono inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2.5 py-1 text-xs text-accent">
            <Gauge className="size-3.5" />
            {escalated.length} escalated: {escalated.join(", ")}
          </span>
        )}
        {dark.length > 0 && (
          <span className="mono inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 px-2.5 py-1 text-xs text-muted">
            <MoonStar className="size-3.5" />
            {dark.length} dark
          </span>
        )}
      </div>
      <div className="max-h-32 overflow-y-auto rounded-ctl border border-border bg-bg p-2">
        <div className="grid grid-cols-2 gap-1 sm:grid-cols-4 lg:grid-cols-6">
          {cameras.map((c) => (
            <label key={c.id} className="flex items-center gap-1.5 text-xs text-muted">
              <input
                type="checkbox"
                checked={selected.includes(c.id)}
                onChange={(e) =>
                  setSelected((prev) =>
                    e.target.checked ? [...prev, c.id] : prev.filter((x) => x !== c.id)
                  )
                }
              />
              <span className="mono">{c.id}</span>
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function ConsoleOverviewPage() {
  const { status, alerts: liveAlerts } = useLive();
  const { data: cameras } = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/api/cameras"),
  });
  const { data: stats } = useQuery({
    queryKey: ["detections-stats"],
    queryFn: () => api<DetectionStats>("/api/detections/stats"),
    refetchInterval: 4000,
  });
  const { data: alertHistory } = useQuery({
    queryKey: ["alerts"],
    queryFn: () => api<Alert[]>("/api/alerts?limit=20"),
    refetchInterval: 15000,
  });

  const healthRows = useMemo(
    () => mergeHealth(cameras ?? [], null, status),
    [cameras, status]
  );

  const camerasOnline = healthRows.filter((c) => c.state === "online").length;
  const activeAlerts = useMemo(() => {
    const acked = new Set(
      (alertHistory ?? []).filter((a) => a.acknowledged).map((a) => a.id)
    );
    const total = (alertHistory ?? []).length + liveAlerts.filter((a) => a.alert_id != null).length;
    const ackedCount = acked.size;
    return Math.max(total - ackedCount, 0);
  }, [alertHistory, liveAlerts]);

  const hasDetections = (stats?.total_detections ?? 0) > 0;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-text">Overview</h1>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <KpiStat
          label="Cameras online"
          value={cameras ? `${camerasOnline}/${cameras.length}` : "–"}
        />
        <KpiStat
          label="Detections"
          value={stats ? stats.total_detections.toLocaleString("en-IN") : "–"}
        />
        <KpiStat
          label="Plates read"
          value={stats ? stats.with_plate.toLocaleString("en-IN") : "–"}
          trend={stats ? `${stats.plate_rate_pct.toFixed(1)}% rate` : undefined}
        />
        <KpiStat label="Active alerts" value={String(activeAlerts)} />
        <KpiStat
          label="Infer time"
          value={status ? `${status.inference.infer_ms.toFixed(0)} ms` : "–"}
        />
      </div>

      {!hasDetections && stats && (
        <EmptyState
          icon={Gauge}
          title="No detections yet"
          body="Start the pipeline to begin analysing feeds."
          action={
            <Gate min="admin" reason="Sign in as admin to start the pipeline">
              <Button
                size="sm"
                onClick={() =>
                  api(`/api/pipeline/start?cameras=${(cameras ?? []).slice(0, 3).map((c) => c.id).join(",")}`, {
                    method: "POST",
                  }).then(() => toast.success("Pipeline starting"))
                }
              >
                <Play className="size-4" />
                Start pipeline
              </Button>
            </Gate>
          }
        />
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <AlertFeed />
        </div>
        <div className="flex flex-col gap-4">
          <HealthStrip cameras={healthRows} />
          <MiniMap cameras={healthRows} />
        </div>
      </div>

      <PipelineControls cameras={cameras ?? []} />
    </div>
  );
}
