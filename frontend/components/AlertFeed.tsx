"use client";
import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, AlertCircle, Info, Bell } from "lucide-react";
import { cn } from "cn";
import { api, apiUrl, ApiError } from "@/lib/api";
import { ago } from "@/lib/time";
import { useLive } from "@/lib/live";
import type { Alert } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Gate } from "@/components/Gate";
import { EmptyState } from "@/components/EmptyState";

const SEVERITY: Record<string, { icon: typeof AlertOctagon; cls: string }> = {
  critical: { icon: AlertOctagon, cls: "text-bad bg-bad/10 border-bad/40" },
  high: { icon: AlertTriangle, cls: "text-warn bg-warn/10 border-warn/40" },
  medium: { icon: AlertCircle, cls: "text-info bg-info/10 border-info/40" },
  low: { icon: Info, cls: "text-muted bg-surface-2 border-border" },
};

/** Unified row shape for both fetched history and live socket events. */
interface Row {
  id: string;
  at: string;
  camera_id: string;
  camera_name: string | null;
  plate: string | null;
  score: number | null;
  severity: string;
  evidence: string | null;
  acknowledged: boolean;
  isNew?: boolean;
}

function fromAlert(a: Alert): Row {
  return {
    id: String(a.id),
    at: a.at,
    camera_id: a.camera_id,
    camera_name: a.camera_name,
    plate: a.plate_observed ?? a.plate_watchlist,
    score: a.score,
    severity: a.severity,
    evidence: a.evidence,
    acknowledged: a.acknowledged,
  };
}

export function AlertFeed() {
  const qc = useQueryClient();
  const { alerts: liveAlerts } = useLive();
  const { data, isLoading } = useQuery({
    queryKey: ["alerts"],
    queryFn: () => api<Alert[]>("/api/alerts?limit=20"),
    refetchInterval: 15000,
  });

  const rows = useMemo(() => {
    const byId = new Map<string, Row>();
    (data ?? []).forEach((a) => byId.set(String(a.id), fromAlert(a)));
    liveAlerts.forEach((m) => {
      if (m.alert_id == null) return;
      const id = String(m.alert_id);
      if (byId.has(id)) return; // already have it from history
      byId.set(id, {
        id,
        at: new Date().toISOString(),
        camera_id: m.camera_id ?? "?",
        camera_name: null,
        plate: (m.plate as string | undefined) ?? null,
        score: m.score ?? null,
        severity: m.severity ?? "medium",
        evidence: m.evidence ?? null,
        acknowledged: false,
        isNew: true,
      });
    });
    return Array.from(byId.values()).sort((a, b) => (a.at < b.at ? 1 : -1));
  }, [data, liveAlerts]);

  const ack = useMutation({
    mutationFn: (id: string) => api(`/api/alerts/${id}/acknowledge`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
    onError: (e: unknown) => {
      if (!(e instanceof ApiError)) throw e;
    },
  });

  if (!isLoading && rows.length === 0) {
    return (
      <EmptyState
        icon={Bell}
        title="No alerts yet"
        body="Watchlist and zone alerts will appear here as soon as the pipeline raises them."
      />
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {rows.map((r) => {
        const sev = SEVERITY[r.severity] ?? SEVERITY.medium;
        const Icon = sev.icon;
        return (
          <div
            key={r.id}
            className={cn(
              "flex flex-wrap items-center gap-2 rounded-card border border-border bg-surface p-2 sm:flex-nowrap sm:gap-3",
              r.isNew && "animate-in fade-in slide-in-from-top-2 duration-200"
            )}
          >
            {r.evidence ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={apiUrl("/evidence/" + r.evidence.replace(/^\/?evidence\//, ""))}
                alt={`Evidence for alert ${r.id}`}
                className="size-12 shrink-0 rounded-[6px] border border-border object-cover"
              />
            ) : (
              <div className="size-12 shrink-0 rounded-[6px] border border-border bg-surface-2" />
            )}
            <span
              className={cn(
                "mono inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                sev.cls
              )}
            >
              <Icon className="size-3.5" />
              {r.severity}
            </span>
            <div className="order-last min-w-0 basis-full flex-1 sm:order-none sm:basis-auto">
              <div className="flex items-center gap-2">
                <span className="mono text-sm text-text">{r.plate ?? "—"}</span>
                <span className="truncate text-xs text-muted">{r.camera_name ?? r.camera_id}</span>
              </div>
              <div className="mono text-xs text-muted">
                {r.score != null ? `${(r.score * 100).toFixed(0)}% match` : ""}
              </div>
            </div>
            <Tooltip>
              <TooltipTrigger render={<span tabIndex={0} className="mono text-xs text-muted" />}>
                {ago(r.at)}
              </TooltipTrigger>
              <TooltipContent>Alert ingested {new Date(r.at).toLocaleString("en-IN", { hour12: false })}</TooltipContent>
            </Tooltip>
            <div className="flex shrink-0 gap-1">
              <Gate min="operator" reason="Sign in as operator or admin to acknowledge alerts">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={r.acknowledged}
                  onClick={() => ack.mutate(r.id)}
                >
                  {r.acknowledged ? "Acknowledged" : "Acknowledge"}
                </Button>
              </Gate>
              {r.evidence && (
                <Button
                  size="sm"
                  variant="ghost"
                  render={
                    <a
                      href={apiUrl("/evidence/" + r.evidence.replace(/^\/?evidence\//, ""))}
                      target="_blank"
                      rel="noreferrer"
                    />
                  }
                >
                  View
                </Button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
