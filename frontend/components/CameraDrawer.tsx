"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw, Radio, Crosshair } from "lucide-react";
import { apiUrl } from "@/lib/api";
import type { Camera } from "@/lib/types";
import type { CamHealthRow } from "@/lib/health";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { STATE_TEXT } from "@/lib/health";

const WALL_KEY = "NETRA_WALL";

export function CameraDrawer({
  camera,
  health,
  open,
  onOpenChange,
}: {
  camera: Camera | null;
  health: CamHealthRow | undefined;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const router = useRouter();
  const [nonce, setNonce] = useState(0);

  if (!camera) return null;

  const snapshotUrl = `${apiUrl(`/api/cameras/${camera.id}/snapshot`)}?t=${nonce}`;

  function openInWall() {
    try {
      const raw = localStorage.getItem(WALL_KEY);
      const arr: string[] = raw ? JSON.parse(raw) : [];
      if (!arr.includes(camera!.id)) arr.push(camera!.id);
      localStorage.setItem(WALL_KEY, JSON.stringify(arr));
    } catch {}
    router.push("/console/wall");
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto p-4 sm:max-w-md">
        <SheetHeader className="p-0">
          <SheetTitle>{camera.name}</SheetTitle>
        </SheetHeader>

        <div className="overflow-hidden rounded-card border border-border bg-surface-2">
          <img
            src={snapshotUrl}
            alt={`Snapshot from ${camera.name}`}
            className="aspect-video w-full object-cover"
            onError={(e) => { (e.target as HTMLImageElement).style.opacity = "0.3"; }}
          />
          <div className="flex items-center justify-between p-2">
            <span className="mono text-xs text-muted">{camera.id}</span>
            <Button size="sm" variant="ghost" onClick={() => setNonce((n) => n + 1)}>
              <RefreshCw className="size-3.5" /> Refresh
            </Button>
          </div>
        </div>

        <table className="mono w-full text-xs">
          <tbody>
            {[
              ["City", camera.city ?? "—"],
              ["District", camera.district ?? "—"],
              ["Department", camera.department ?? "—"],
              ["Capability", camera.capability],
              ["Codec", camera.codec ?? "—"],
              ["Resolution", camera.width && camera.height ? `${camera.width}x${camera.height}` : "—"],
              ["FPS (declared)", camera.declared_fps ?? "—"],
              ["Lat, Lon", `${camera.lat}, ${camera.lon}`],
              ["Time group", camera.time_group ?? "—"],
              ["Health (state)", health?.state ?? camera.health],
              ["Measured FPS", health?.fps != null ? health.fps.toFixed(1) : "—"],
              ["Stale (s)", health?.stale_s != null ? health.stale_s.toFixed(1) : "—"],
              ["Reconnects", String(health?.reconnects ?? 0)],
            ].map(([k, v]) => (
              <tr key={k} className="border-b border-border/60 last:border-0">
                <td className="py-1.5 pr-3 text-muted">{k}</td>
                <td className={`py-1.5 ${k === "Health (state)" ? STATE_TEXT[(health?.state ?? "not-started")] : "text-text"}`}>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {camera.capability_note && (
          <Badge variant="outline" className="w-fit text-warn">{camera.capability_note}</Badge>
        )}

        <div className="mt-auto flex flex-col gap-2">
          <Button onClick={openInWall}>
            <Radio className="size-4" /> Open in wall
          </Button>
          <Button variant="secondary" onClick={() => router.push(`/console/vehicles?camera=${camera.id}`)}>
            <Crosshair className="size-4" /> Trace here
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
