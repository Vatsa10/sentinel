"use client";
import { Suspense, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, Plus, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useLive } from "@/lib/live";
import type { Camera } from "@/lib/types";
import { mergeHealth } from "@/lib/health";
import { Gate } from "@/components/Gate";
import { CameraDrawer } from "@/components/CameraDrawer";
import { OnboardDrawer } from "@/components/OnboardDrawer";
import { GapPanel, type GapAnalysis } from "@/components/GapPanel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import type { GapZone } from "@/components/MapView";

const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => <Skeleton className="h-full w-full" />,
});

export default function MapPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[calc(100vh-7rem)] w-full rounded-card" />}>
      <MapPageInner />
    </Suspense>
  );
}

function MapPageInner() {
  const search = useSearchParams();
  const qc = useQueryClient();
  const { status } = useLive();

  const { data: cameras } = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/api/cameras"),
  });
  const { data: gap } = useQuery({
    queryKey: ["gap-analysis"],
    queryFn: () => api<GapAnalysis>("/api/cameras/gap-analysis"),
  });

  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(search.get("camera"));
  const [focusId, setFocusId] = useState<string | null>(search.get("camera"));
  const [onboardOpen, setOnboardOpen] = useState(false);
  const [layers, setLayers] = useState({ coverage: false, gaps: true, state: true });

  const healthRows = useMemo(() => mergeHealth(cameras ?? [], null, status), [cameras, status]);
  const healthMap = useMemo(() => new Map(healthRows.map((h) => [h.camera_id, h])), [healthRows]);

  const filtered = useMemo(() => {
    const list = cameras ?? [];
    if (!query.trim()) return list;
    const q = query.trim().toLowerCase();
    return list.filter((c) => c.id.toLowerCase().includes(q) || c.name.toLowerCase().includes(q));
  }, [cameras, query]);

  const gapZones: GapZone[] = useMemo(() => {
    const byId = new Map((cameras ?? []).map((c) => [c.id, c]));
    return (gap?.degraded_cameras ?? [])
      .map((g) => {
        const cam = byId.get(g.id);
        if (!cam) return null;
        return { id: g.id, name: g.name, lat: cam.lat, lon: cam.lon, reason: g.reason };
      })
      .filter((g): g is GapZone => !!g);
  }, [gap, cameras]);

  const selectedCamera = (cameras ?? []).find((c) => c.id === selectedId) ?? null;

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-text">GIS Map</h1>
        <Gate min="admin" reason="Onboarding cameras requires an admin key">
          <Button onClick={() => setOnboardOpen(true)}>
            <Plus className="size-4" /> Onboard camera
          </Button>
        </Gate>
      </div>

      <div className="relative flex-1 overflow-hidden rounded-card border border-border">
        <MapView
          cameras={filtered}
          health={healthMap}
          gapZones={gapZones}
          layers={layers}
          focusId={focusId}
          onSelect={setSelectedId}
        />

        <div className="pointer-events-none absolute inset-0 z-[1000]">
          <div className="pointer-events-auto absolute left-3 top-3 flex w-64 flex-col gap-3 rounded-card border border-border bg-surface/95 p-3 shadow-lg backdrop-blur">
            <div className="relative">
              <Search className="absolute left-2 top-2.5 size-3.5 text-muted" />
              <Input
                className="pl-7"
                placeholder="Search id or name…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="flex items-center gap-2 text-sm">
              <Layers className="size-4 text-muted" />
              <span className="text-xs font-medium text-muted">Layers</span>
            </div>
            <LayerToggle label="Health state colours" checked={layers.state} onChange={(v) => setLayers((l) => ({ ...l, state: v }))} />
            <LayerToggle label="Coverage circles (150 m)" checked={layers.coverage} onChange={(v) => setLayers((l) => ({ ...l, coverage: v }))} />
            <LayerToggle label="Gap zones" checked={layers.gaps} onChange={(v) => setLayers((l) => ({ ...l, gaps: v }))} />
            <p className="text-[11px] text-muted">{filtered.length} of {cameras?.length ?? 0} cameras shown</p>
          </div>
        </div>
      </div>

      <GapPanel data={gap} onFocus={(id) => { setFocusId(id); setSelectedId(id); }} />

      <CameraDrawer
        camera={selectedCamera}
        health={selectedCamera ? healthMap.get(selectedCamera.id) : undefined}
        open={!!selectedCamera}
        onOpenChange={(v) => { if (!v) setSelectedId(null); }}
        onDeleted={() => { setSelectedId(null); qc.invalidateQueries({ queryKey: ["cameras"] }); }}
      />

      <OnboardDrawer
        open={onboardOpen}
        onOpenChange={setOnboardOpen}
        onDone={() => qc.invalidateQueries({ queryKey: ["cameras"] })}
      />
    </div>
  );
}

function LayerToggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <Label className="text-xs font-normal text-text">{label}</Label>
      <Switch size="sm" checked={checked} onCheckedChange={onChange} />
    </div>
  );
}
