"use client";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { api, apiUrl } from "@/lib/api";
import { useLive } from "@/lib/live";
import type { ZoneEvent } from "@/lib/types";
import { TimeBadge } from "@/components/TimeBadge";
import { EmptyState } from "@/components/EmptyState";

export function ZoneEventList() {
  const { alerts: liveAlerts } = useLive();
  const { data, isLoading } = useQuery({
    queryKey: ["zone-events"],
    queryFn: () => api<ZoneEvent[]>("/api/zones/events?limit=100"),
    refetchInterval: 4000,
  });

  const rows = useMemo(() => {
    const byId = new Map<string, ZoneEvent>();
    (data ?? []).forEach((e) => byId.set(String(e.id), e));
    liveAlerts.forEach((m) => {
      if (m.kind !== "zone" || m.id == null) return;
      const id = String(m.id);
      if (byId.has(id)) return;
      byId.set(id, {
        id: Number(id),
        at: (m.at as string) ?? new Date().toISOString(),
        camera_id: (m.camera_id as string) ?? "?",
        camera_name: null,
        lat: null,
        lon: null,
        zone: (m.zone as string) ?? null,
        rule: (m.rule as string) ?? "intrusion",
        object_class: (m.object_class as string) ?? null,
        direction: (m.direction as string) ?? null,
        detail: (m.detail as string) ?? null,
        severity: (m.severity as string) ?? "medium",
        evidence: (m.evidence as string) ?? null,
        acknowledged: false,
      });
    });
    return Array.from(byId.values()).sort((a, b) => (a.at < b.at ? 1 : -1));
  }, [data, liveAlerts]);

  if (!isLoading && rows.length === 0) {
    return <EmptyState icon={ShieldAlert} title="No zone events" body="Intrusion, crossing and loitering events will appear here." />;
  }

  return (
    <div className="flex flex-col gap-2">
      {rows.map((e) => (
        <div key={e.id} className="flex items-center gap-3 rounded-card border border-border bg-surface p-2">
          {e.evidence ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={apiUrl(e.evidence)} alt="" className="size-12 shrink-0 rounded-[6px] border border-border object-cover" />
          ) : (
            <div className="size-12 shrink-0 rounded-[6px] border border-border bg-surface-2" />
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{e.zone ?? e.rule}</span>
              <span className="mono text-xs text-muted">{e.camera_name ?? e.camera_id}</span>
            </div>
            <div className="text-xs text-muted">{e.rule}{e.object_class ? ` · ${e.object_class}` : ""}{e.direction ? ` · ${e.direction}` : ""}{e.detail ? ` · ${e.detail}` : ""}</div>
          </div>
          <TimeBadge det={{ pts_ms: 0, scene_time: e.at, scene_time_corroborated: true }} />
        </div>
      ))}
    </div>
  );
}
