"use client";
import { Suspense, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Crosshair, ExternalLink, Search } from "lucide-react";
import { cn } from "cn";
import { api, apiUrl } from "@/lib/api";
import type { Camera, Detection, DetectionPage } from "@/lib/types";
import { DetectionTable } from "@/components/DetectionTable";
import { VehicleDetail } from "@/components/VehicleDetail";
import { EmptyState } from "@/components/EmptyState";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

const RouteMap = dynamic(() => import("@/components/RouteMap"), {
  ssr: false,
  loading: () => <Skeleton className="h-72 w-full rounded-card" />,
});

const CLASSES = ["car", "motorcycle", "truck", "bus", "auto"];

interface RouteHop {
  camera_id: string;
  camera_name: string;
  lat: number | null;
  lon: number | null;
  at: string;
  plate_text: string | null;
  vehicle_class: string | null;
  colour: string | null;
  evidence_path: string | null;
  detection_id: number;
  leg_km: number | null;
  leg_seconds: number | null;
  implied_kmh: number | null;
}
interface RouteResult {
  query: string;
  hops: RouteHop[];
  rejected: { camera_id: string; reason: string }[];
  total_km: number;
  duration_s: number;
  time_groups: string[];
  hop_count: number;
}

export default function VehiclesPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full rounded-card" />}>
      <VehiclesPageInner />
    </Suspense>
  );
}

function VehiclesPageInner() {
  const search = useSearchParams();

  const [plate, setPlate] = useState("");
  const [attrText, setAttrText] = useState("");
  const [cameraId, setCameraId] = useState(search.get("camera") ?? "");
  const [sinceMinutes, setSinceMinutes] = useState<string>("");
  const [classes, setClasses] = useState<string[]>([]);
  const [routeQuery, setRouteQuery] = useState<string | null>(null);
  const [selected, setSelected] = useState<Detection | null>(null);

  const { data: cameras } = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/api/cameras"),
  });

  const params = useMemo(() => {
    const p = new URLSearchParams();
    if (plate.trim()) p.set("plate", plate.trim().toUpperCase());
    if (cameraId) p.set("camera_id", cameraId);
    if (sinceMinutes) p.set("since_minutes", sinceMinutes);
    if (classes.length === 1) p.set("vehicle_class", classes[0]);
    p.set("limit", "100");
    return p.toString();
  }, [plate, cameraId, sinceMinutes, classes]);

  const { data: page, isFetching } = useQuery({
    queryKey: ["detections", params],
    queryFn: () => api<DetectionPage>(`/api/detections?${params}`),
  });

  // ponytail: /api/detections has no description/attribute filter param
  // (app.py:403), so a free-text attribute query is matched client-side over
  // the returned page only — the helper line below says so.
  const filtered = useMemo(() => {
    let items = page?.items ?? [];
    if (classes.length > 1) items = items.filter((d) => d.vehicle_class && classes.includes(d.vehicle_class));
    const q = attrText.trim().toLowerCase();
    if (q) {
      items = items.filter((d) => {
        const desc = (d.attributes as { description?: string } | null)?.description ?? "";
        return (
          (d.colour ?? "").toLowerCase().includes(q) ||
          (d.vehicle_class ?? "").toLowerCase().includes(q) ||
          desc.toLowerCase().includes(q)
        );
      });
    }
    return items;
  }, [page, classes, attrText]);

  const { data: route, isFetching: routeLoading } = useQuery({
    queryKey: ["route", routeQuery],
    queryFn: () => api<RouteResult>(`/api/route?plate=${encodeURIComponent(routeQuery!)}`),
    enabled: !!routeQuery,
  });

  function traceRegistration() {
    if (!plate.trim()) return;
    setRouteQuery(plate.trim().toUpperCase());
  }

  const routeCameraCount = route ? new Set(route.hops.map((h) => h.camera_id)).size : 0;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-text">Vehicles</h1>

      <div className="flex flex-col gap-3 rounded-card border border-border bg-surface p-3">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <div className="relative">
            <Search className="absolute left-2 top-2.5 size-3.5 text-muted" />
            <Input
              className="mono pl-7 uppercase"
              placeholder="Registration number (e.g. GJ01AB1234)"
              value={plate}
              onChange={(e) => setPlate(e.target.value)}
            />
          </div>
          <Input
            placeholder="Attributes (colour, type, make)…"
            value={attrText}
            onChange={(e) => setAttrText(e.target.value)}
          />
          <select
            className="h-9 rounded-ctl border border-border bg-surface-2 px-2 text-sm text-text"
            value={cameraId}
            onChange={(e) => setCameraId(e.target.value)}
          >
            <option value="">All cameras</option>
            {(cameras ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <select
            className="h-9 rounded-ctl border border-border bg-surface-2 px-2 text-sm text-text"
            value={sinceMinutes}
            onChange={(e) => setSinceMinutes(e.target.value)}
          >
            <option value="">Any time</option>
            <option value="15">Last 15 min</option>
            <option value="60">Last hour</option>
            <option value="1440">Last 24h</option>
          </select>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {CLASSES.map((c) => (
            <button
              key={c}
              onClick={() => setClasses((cs) => (cs.includes(c) ? cs.filter((x) => x !== c) : [...cs, c]))}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs capitalize transition",
                classes.includes(c) ? "border-accent bg-accent/15 text-accent" : "border-border text-muted hover:text-text"
              )}
            >
              {c}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            <a
              href={apiUrl(`/api/export/detections.csv${plate.trim() ? `?plate=${encodeURIComponent(plate.trim())}` : ""}`)}
              className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
            >
              <ExternalLink className="size-3.5" /> Export CSV
            </a>
            <Button onClick={traceRegistration} disabled={!plate.trim()}>
              <Crosshair className="size-4" /> Trace registration number
            </Button>
          </div>
        </div>
        {attrText.trim() && (
          <p className="text-[11px] text-muted">
            Attribute search has no server-side filter — matched client-side over this page&apos;s {page?.count ?? 0} results only.
          </p>
        )}
      </div>

      {routeQuery && (
        <div className="flex flex-col gap-3 rounded-card border border-border bg-surface p-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text">Route: {routeQuery}</h2>
            {routeLoading && <span className="text-xs text-muted">Tracing…</span>}
          </div>
          {route && (
            <>
              <p className="mono text-sm text-text">
                {route.hop_count > 0
                  ? `${route.hop_count} sighting${route.hop_count === 1 ? "" : "s"} on ${routeCameraCount} camera${routeCameraCount === 1 ? "" : "s"}`
                  : "No sightings of this number in the indexed period."}
              </p>
              {route.hop_count > 0 && (
                <>
                  <RouteMap points={route.hops.map((h) => ({ camera_id: h.camera_id, camera_name: h.camera_name, lat: h.lat, lon: h.lon, at: h.at }))} />
                  <div className="flex flex-col gap-1">
                    {route.hops.map((h, i) => (
                      <div key={h.detection_id} className="flex flex-wrap items-center gap-2 rounded-ctl border border-border p-2 text-xs">
                        <span className="mono">#{i + 1}</span>
                        <span className="mono">{h.camera_name}</span>
                        <span className="text-muted">{new Date(h.at).toLocaleString("en-IN", { hour12: false })}</span>
                        <span
                          className="mono ml-auto"
                          title={h.leg_km == null ? "stream time only" : undefined}
                        >
                          {h.leg_km != null && h.leg_seconds != null
                            ? `${h.leg_km} km · ${Math.round(h.leg_seconds)}s`
                            : "—"}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
              {route.rejected.length > 0 && (
                <details className="text-xs text-muted">
                  <summary className="cursor-pointer">{route.rejected.length} sighting(s) listed but not chained</summary>
                  <ul className="mt-1 list-disc space-y-1 pl-4">
                    {route.rejected.map((r, i) => (
                      <li key={i}>{r.camera_id}: {r.reason}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </div>
      )}

      {isFetching && !page ? (
        <Skeleton className="h-64 w-full rounded-card" />
      ) : filtered.length === 0 ? (
        <EmptyState icon={Search} title="No detections match" body="Adjust the filters or clear the search." />
      ) : (
        <DetectionTable items={filtered} onRowClick={setSelected} />
      )}

      <VehicleDetail detection={selected} open={!!selected} onOpenChange={(v) => { if (!v) setSelected(null); }} />
    </div>
  );
}
