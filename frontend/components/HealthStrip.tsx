"use client";
import Link from "next/link";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "cn";
import { STATE_COLOUR, type CamHealthRow, type CamState } from "@/lib/health";

const LEGEND: { state: CamState; label: string }[] = [
  { state: "online", label: "Online" },
  { state: "degraded", label: "Degraded" },
  { state: "offline", label: "Offline" },
  { state: "not-started", label: "Not started" },
];

/** Grid of small squares, one per camera, coloured by health state. */
export function HealthStrip({ cameras }: { cameras: CamHealthRow[] }) {
  return (
    <div className="rounded-card border border-border bg-surface p-3">
      <div className="mb-2 text-xs font-medium text-muted">
        Camera health ({cameras.length})
      </div>
      <div className="grid grid-cols-10 gap-1.5 sm:grid-cols-12">
        {cameras.map((c) => (
          <Tooltip key={c.camera_id}>
            <TooltipTrigger
              render={
                <Link
                  href={`/console/map?camera=${c.camera_id}`}
                  aria-label={`${c.name}: ${c.state}`}
                  className={cn(
                    "size-4 rounded-[3px] focus-visible:ring-2 focus-visible:ring-accent",
                    STATE_COLOUR[c.state]
                  )}
                />
              }
            />
            <TooltipContent>
              <div className="mono">{c.camera_id}</div>
              <div>{c.name}</div>
              <div>
                {c.state}
                {c.fps != null ? ` · ${c.fps.toFixed(1)} fps` : ""}
                {c.stale_s != null ? ` · stale ${c.stale_s.toFixed(0)}s` : ""}
              </div>
              {c.codec && <div className="mono opacity-70">{c.codec}</div>}
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
        {LEGEND.map((l) => (
          <span key={l.state} className="flex items-center gap-1.5 text-xs text-muted">
            <span className={cn("size-2.5 rounded-[2px]", STATE_COLOUR[l.state])} />
            {l.label}
          </span>
        ))}
      </div>
    </div>
  );
}
