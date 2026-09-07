"use client";
import { Badge } from "@/components/ui/badge";
import type { ClonedPlate } from "@/lib/types";

// ponytail: /api/analytics/cloned-plates' sighting objects (netra/analytics/
// cloned_plate.py _sighting_dict) carry no evidence path, so there is no
// crop URL to render here — the card links into Vehicles instead, where the
// detection's evidence crop does render (VehicleDetail).
function Crop({ label, detectionId }: { label: string; detectionId: number }) {
  return (
    <a
      href={`/console/vehicles?detection=${detectionId}`}
      className="flex aspect-video w-full flex-col items-center justify-center gap-1 rounded-ctl border border-border bg-surface-2 text-center text-muted hover:text-text"
    >
      <span className="mono text-[11px]">{label}</span>
      <span className="text-[10px] underline">open in Vehicles</span>
    </a>
  );
}

export function ClonePairCard({ finding }: { finding: ClonedPlate }) {
  return (
    <div className="flex flex-col gap-3 rounded-card border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mono text-sm font-semibold text-text">{finding.plate}</span>
        <Badge variant="outline">confidence {(finding.confidence * 100).toFixed(0)}%</Badge>
        <span className="ml-auto text-xs text-muted">{finding.distance_km} km · {Math.round(finding.elapsed_s)}s</span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Crop label={`${finding.sighting_a.camera_name ?? finding.sighting_a.camera_id} · ${new Date(finding.sighting_a.at).toLocaleTimeString("en-IN", { hour12: false })}`} detectionId={finding.sighting_a.detection_id} />
        <Crop label={`${finding.sighting_b.camera_name ?? finding.sighting_b.camera_id} · ${new Date(finding.sighting_b.at).toLocaleTimeString("en-IN", { hour12: false })}`} detectionId={finding.sighting_b.detection_id} />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="text-muted">
          implied speed:{" "}
          <span className="mono text-text">{finding.implied_kmh != null ? `${finding.implied_kmh} km/h` : "—"}</span>
        </span>
        <Badge className="border-bad/40 bg-bad/10 text-bad">{finding.reason}</Badge>
      </div>
    </div>
  );
}
