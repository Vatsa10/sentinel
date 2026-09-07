"use client";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { EmptyState } from "@/components/EmptyState";
import { AlertTriangle } from "lucide-react";
import type { AnomaliesResponse } from "@/lib/types";

/**
 * Shared per-camera anomaly table (camera, bucket hour, observed vs
 * expected, z-score, status), used by both the Traffic page and the
 * Intelligence page's Anomalies tab so the two never drift apart.
 */
export function AnomalyTable({ data }: { data: AnomaliesResponse | undefined }) {
  if (!data || data.assessments.length === 0) {
    return <EmptyState icon={AlertTriangle} title="No anomaly data" body="Baselines need more history before anything can be judged." />;
  }
  return (
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
        {data.assessments.map((a, i) => (
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
  );
}
