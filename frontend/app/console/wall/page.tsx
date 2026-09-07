"use client";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Grid2x2, Grid3x3, LayoutGrid, Square } from "lucide-react";
import { cn } from "cn";
import { api } from "@/lib/api";
import type { Camera } from "@/lib/types";
import { mergeHealth } from "@/lib/health";
import { useLive } from "@/lib/live";
import { CameraTile } from "@/components/CameraTile";
import { CameraPicker, TIME_ALIGNED_PRESET } from "@/components/CameraPicker";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";

const STORAGE_KEY = "NETRA_WALL";

const LAYOUTS = [
  { n: 1, cols: "grid-cols-1", icon: Square },
  { n: 4, cols: "grid-cols-1 sm:grid-cols-2", icon: Grid2x2 },
  { n: 9, cols: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3", icon: Grid3x3 },
  { n: 16, cols: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-4", icon: LayoutGrid },
] as const;

function loadWall(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr) && arr.length > 0) return arr;
    }
  } catch {}
  return TIME_ALIGNED_PRESET;
}

export default function VideoWallPage() {
  const [ids, setIds] = useState<string[] | null>(null);
  const [layout, setLayout] = useState<1 | 4 | 9 | 16>(9);
  const { status } = useLive();

  const { data: cameras } = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/api/cameras"),
  });

  // Load persisted tile list (and mobile 1x1 default) on mount, client-side only.
  useEffect(() => {
    setIds(loadWall());
    if (typeof window !== "undefined" && window.innerWidth < 640) setLayout(1);
  }, []);

  useEffect(() => {
    if (ids !== null) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
      } catch {}
    }
  }, [ids]);

  const byId = useMemo(() => new Map((cameras ?? []).map((c) => [c.id, c])), [cameras]);
  const tiles = (ids ?? []).map((id) => byId.get(id)).filter((c): c is Camera => !!c);
  const healthRows = useMemo(
    () => mergeHealth(cameras ?? [], null, status),
    [cameras, status]
  );
  const healthById = useMemo(() => new Map(healthRows.map((h) => [h.camera_id, h])), [healthRows]);

  const activeLayout = LAYOUTS.find((l) => l.n === layout) ?? LAYOUTS[2];
  const visible = tiles.slice(0, layout);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-text">Video Wall</h1>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1 rounded-ctl border border-border bg-surface p-1">
            {LAYOUTS.map((l) => (
              <Button
                key={l.n}
                size="sm"
                variant={layout === l.n ? "default" : "ghost"}
                className="h-9 sm:h-7"
                aria-label={`${l.n}-tile layout`}
                onClick={() => setLayout(l.n)}
              >
                <l.icon className="size-4" />
                {l.n}
              </Button>
            ))}
          </div>
          <CameraPicker
            cameras={cameras ?? []}
            current={ids ?? []}
            onAdd={(id) => setIds((prev) => (prev?.includes(id) ? prev : [...(prev ?? []), id]))}
            onAddMany={(newIds) =>
              setIds((prev) => Array.from(new Set([...(prev ?? []), ...newIds])))
            }
          />
        </div>
      </div>

      {tiles.length === 0 ? (
        <EmptyState
          icon={Grid2x2}
          title="No cameras on the wall"
          body="Add cameras from the picker to start watching live feeds."
        />
      ) : (
        <div className={cn("grid gap-3", activeLayout.cols)}>
          {visible.map((cam) => (
            <CameraTile
              key={cam.id}
              cam={cam}
              health={healthById.get(cam.id)}
              onRemove={() => setIds((prev) => (prev ?? []).filter((id) => id !== cam.id))}
            />
          ))}
        </div>
      )}
    </div>
  );
}
