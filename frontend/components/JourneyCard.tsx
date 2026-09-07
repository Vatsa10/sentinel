"use client";
import dynamic from "next/dynamic";
import { ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { Journey } from "@/lib/types";

const RouteMap = dynamic(() => import("@/components/RouteMap"), {
  ssr: false,
  loading: () => <Skeleton className="h-40 w-full rounded-card" />,
});

export function JourneyCard({ journey }: { journey: Journey }) {
  return (
    <div className="flex flex-col gap-3 rounded-card border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-text">{journey.hop_count} hops · {journey.total_km} km · {Math.round(journey.elapsed_s)}s</span>
        <Badge variant="outline">confidence {(journey.confidence * 100).toFixed(0)}%</Badge>
        {journey.truncated && <Badge className="text-warn">truncated</Badge>}
        <span className="ml-auto text-xs text-muted">{journey.time_group}</span>
      </div>

      <div className="mono flex flex-wrap items-center gap-1.5 text-xs">
        {journey.hops.map((h, i) => (
          <span key={h.detection_id} className="flex items-center gap-1.5">
            <span className="rounded-full border border-border bg-surface-2 px-2 py-0.5">
              {h.camera_id}
              {h.leg_seconds != null && i > 0 ? "" : ""}
            </span>
            {i < journey.hops.length - 1 && (
              <span className="flex items-center gap-1 text-muted">
                <ArrowRight className="size-3" />
                {journey.hops[i + 1]?.leg_seconds != null ? `${Math.round(journey.hops[i + 1].leg_seconds!)}s` : ""}
              </span>
            )}
          </span>
        ))}
      </div>

      <RouteMap
        points={journey.hops.map((h) => ({ camera_id: h.camera_id, camera_name: h.camera_name, lat: h.lat, lon: h.lon, at: h.at }))}
      />

      {journey.note && <p className="text-[11px] text-muted">{journey.note}</p>}
    </div>
  );
}
