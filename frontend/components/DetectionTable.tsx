"use client";
import { apiUrl } from "@/lib/api";
import type { Detection } from "@/lib/types";
import { TimeBadge } from "@/components/TimeBadge";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export function DetectionTable({
  items,
  onRowClick,
}: {
  items: Detection[];
  onRowClick: (d: Detection) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-card border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Crop</TableHead>
            <TableHead>Plate</TableHead>
            <TableHead>Class</TableHead>
            <TableHead>Colour</TableHead>
            <TableHead>Camera</TableHead>
            <TableHead>Time</TableHead>
            <TableHead>Confidence</TableHead>
            <TableHead>Description</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((d) => (
            <TableRow key={d.id} className="cursor-pointer hover:bg-surface-2" onClick={() => onRowClick(d)}>
              <TableCell>
                {d.evidence ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={apiUrl(d.evidence)} alt="" className="h-12 w-16 rounded object-cover" />
                ) : (
                  <div className="h-12 w-16 rounded bg-surface-2" />
                )}
              </TableCell>
              <TableCell className="mono">
                <div className="flex items-center gap-1">
                  {d.plate_text ?? "—"}
                  {d.plate_votes != null && d.plate_votes > 0 && (
                    <Badge variant="outline" className="text-[10px]">{d.plate_votes}v</Badge>
                  )}
                </div>
              </TableCell>
              <TableCell>{d.vehicle_class ?? "—"}</TableCell>
              <TableCell>{d.colour ?? "—"}</TableCell>
              <TableCell className="mono">{d.camera_name ?? d.camera_id}</TableCell>
              <TableCell><TimeBadge det={d} /></TableCell>
              <TableCell>
                <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-2">
                  <div className="h-full bg-accent" style={{ width: `${Math.round(d.confidence * 100)}%` }} />
                </div>
              </TableCell>
              <TableCell className="max-w-[16rem] truncate text-muted">
                {(d.attributes as { description?: string } | null)?.description ?? "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
