"use client";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { api } from "@/lib/api";
import type { TrafficLive, AnomaliesResponse, BaselinesResponse } from "@/lib/types";
import { CountsChart, type CountsPoint } from "@/components/charts/CountsChart";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface HistoryRow {
  camera_id: string;
  at: string;
  total: number;
  cumulative_total: number;
  loops_seen: number;
  counts_by_class: Record<string, number> | null;
  directions: Record<string, number> | null;
  mean_dwell_s: number | null;
}

export default function TrafficPage() {
  const { data: live } = useQuery({
    queryKey: ["traffic-live"],
    queryFn: () => api<TrafficLive>("/api/traffic/live"),
    refetchInterval: 4000,
  });

  const [selected, setSelected] = useState<string>("");
  const cameraId = selected || live?.cameras?.[0]?.camera_id || "";

  const { data: history, isLoading: historyLoading } = useQuery({
    queryKey: ["traffic-history", cameraId],
    queryFn: () => api<HistoryRow[]>(`/api/traffic/history?camera_id=${encodeURIComponent(cameraId)}&limit=200`),
    enabled: !!cameraId,
  });

  const { data: baselines } = useQuery({
    queryKey: ["baselines", cameraId],
    queryFn: () => api<BaselinesResponse>(`/api/analytics/baselines?camera_id=${encodeURIComponent(cameraId)}`),
    enabled: !!cameraId,
  });

  const { data: anomalies } = useQuery({
    queryKey: ["anomalies"],
    queryFn: () => api<AnomaliesResponse>("/api/analytics/anomalies?include_normal=true"),
    refetchInterval: 8000,
  });

  const chartData: CountsPoint[] = useMemo(() => {
    if (!history) return [];
    const byHour = new Map((baselines?.baselines ?? []).filter((b) => b.camera_id === cameraId).map((b) => [b.hour, b]));
    return [...history]
      .reverse()
      .map((r) => {
        const hour = new Date(r.at).getUTCHours();
        const b = byHour.get(hour);
        return {
          bucket: new Date(r.at).toLocaleTimeString("en-IN", { hour12: false, hour: "2-digit", minute: "2-digit" }),
          count: r.total,
          expectedLow: b?.sufficient ? Math.max(0, b.mean - 2 * b.effective_stdev) : null,
          expectedHigh: b?.sufficient ? b.mean + 2 * b.effective_stdev : null,
        };
      });
  }, [history, baselines, cameraId]);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-text">Traffic</h1>

      {!live || live.cameras.length === 0 ? (
        <EmptyState icon={Activity} title="No live traffic yet" body="Start the pipeline to see per-camera counts." />
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {live.cameras.map((c) => (
            <button
              key={c.camera_id}
              onClick={() => setSelected(c.camera_id)}
              className={`flex flex-col gap-1 rounded-card border p-3 text-left transition ${
                c.camera_id === cameraId ? "border-accent bg-accent/10" : "border-border bg-surface hover:bg-surface-2"
              }`}
            >
              <span className="mono text-xs text-muted">{c.camera_id}</span>
              <span className="text-xl font-semibold text-text">{c.total}</span>
              <span className="text-[11px] text-muted">cumulative {c.cumulative_total} · loops {c.loops_seen}</span>
              {c.counts_by_class && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {Object.entries(c.counts_by_class).map(([cls, n]) => (
                    <Badge key={cls} variant="outline" className="text-[10px]">{cls}: {n}</Badge>
                  ))}
                </div>
              )}
            </button>
          ))}
        </div>
      )}

      <div className="rounded-card border border-border bg-surface p-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text">History — {cameraId || "select a camera"}</h2>
        </div>
        {historyLoading ? (
          <Skeleton className="h-64 w-full rounded-card" />
        ) : chartData.length === 0 ? (
          <EmptyState icon={Activity} title="No history yet" body="Traffic snapshots accumulate over time; check back shortly." />
        ) : (
          <CountsChart data={chartData} label="vehicles" />
        )}
      </div>

      <div className="rounded-card border border-border bg-surface p-3">
        <h2 className="mb-2 text-sm font-semibold text-text">Anomalies</h2>
        {!anomalies || anomalies.assessments.length === 0 ? (
          <EmptyState icon={Activity} title="No anomaly data" body="Baselines need more history before anything can be judged." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Camera</TableHead>
                <TableHead>Hour</TableHead>
                <TableHead>Observed</TableHead>
                <TableHead>Expected</TableHead>
                <TableHead>Z</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {anomalies.assessments.map((a, i) => (
                <TableRow key={`${a.camera_id}-${a.hour}-${i}`} className={a.anomalous ? "bg-warn/5" : undefined}>
                  <TableCell className="mono">{a.camera_id}</TableCell>
                  <TableCell className="mono">{a.hour}:00</TableCell>
                  <TableCell className="mono">{a.observed}</TableCell>
                  <TableCell className="mono">{a.baseline ? `${a.baseline.mean.toFixed(1)} ± ${a.baseline.effective_stdev.toFixed(1)}` : "—"}</TableCell>
                  <TableCell className="mono">{a.z_score != null ? a.z_score.toFixed(2) : "—"}</TableCell>
                  <TableCell>
                    {a.status === "stale" ? (
                      <Tooltip>
                        <TooltipTrigger render={<span tabIndex={0} className="text-muted" />}>stale</TooltipTrigger>
                        <TooltipContent>baseline too old to judge</TooltipContent>
                      </Tooltip>
                    ) : (
                      <span className={a.anomalous ? "text-warn" : "text-muted"}>{a.status}</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
