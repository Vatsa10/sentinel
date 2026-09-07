"use client";
import { ExternalLink, MapPinned } from "lucide-react";
import { apiUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export interface GapAnalysis {
  total_cameras: number;
  by_capability: Record<string, number>;
  by_city: Record<string, number>;
  degraded_cameras: { id: string; name: string; city: string | null; reason: string; mean_luma: number | null }[];
  anpr_capable: string[];
  anpr_coverage_pct: number;
  usable_pct: number;
  findings: string[];
}

export function GapPanel({
  data,
  onFocus,
}: {
  data: GapAnalysis | undefined;
  onFocus: (cameraId: string) => void;
}) {
  if (!data) return null;
  return (
    <div className="flex flex-col gap-3 rounded-card border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-text">Gap analysis</h2>
        <a
          href={apiUrl("/api/export/detections.csv")}
          className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
        >
          <ExternalLink className="size-3.5" /> Export CSV
        </a>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <Stat label="Total cameras" value={String(data.total_cameras)} />
        <Stat label="ANPR coverage" value={`${data.anpr_coverage_pct}%`} />
        <Stat label="Usable" value={`${data.usable_pct}%`} />
        <Stat label="Degraded" value={String(data.degraded_cameras.length)} />
      </div>
      <ul className="list-disc space-y-1 pl-4 text-xs text-muted">
        {data.findings.map((f) => <li key={f}>{f}</li>)}
      </ul>
      <div className="max-h-56 overflow-y-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Camera</TableHead>
              <TableHead>City</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.degraded_cameras.map((g) => (
              <TableRow key={g.id}>
                <TableCell className="mono">{g.name}</TableCell>
                <TableCell>{g.city ?? "—"}</TableCell>
                <TableCell className="text-warn">{g.reason}</TableCell>
                <TableCell>
                  <Button size="sm" variant="ghost" onClick={() => onFocus(g.id)}>
                    <MapPinned className="size-3.5" /> Show on map
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-ctl border border-border bg-surface-2 p-2">
      <div className="mono text-base text-text">{value}</div>
      <div className="text-muted">{label}</div>
    </div>
  );
}
