"use client";
import { MapContainer, CircleMarker, Polyline, Tooltip as LeafletTooltip } from "react-leaflet";
import { DarkTiles, MapDarkStyle, DARK_TILE_FILTER_CLASS } from "@/components/map/leafletBase";

export interface RoutePoint {
  camera_id: string;
  camera_name?: string | null;
  lat: number | null;
  lon: number | null;
  at: string;
}

/** Polyline + markers for a plate-route or appearance-track result. */
export default function RouteMap({ points }: { points: RoutePoint[] }) {
  const valid = points.filter((p) => p.lat != null && p.lon != null) as (RoutePoint & { lat: number; lon: number })[];
  const center: [number, number] = valid.length > 0 ? [valid[0].lat, valid[0].lon] : [22.5, 71.5];
  const path: [number, number][] = valid.map((p) => [p.lat, p.lon]);

  return (
    <div className={`h-72 w-full overflow-hidden rounded-card border border-border ${DARK_TILE_FILTER_CLASS}`}>
      <MapDarkStyle />
      <MapContainer center={center} zoom={valid.length ? 10 : 7} className="h-full w-full">
        <DarkTiles />
        {path.length > 1 && <Polyline positions={path} pathOptions={{ color: "#FF6B00", weight: 3 }} />}
        {valid.map((p, i) => (
          <CircleMarker
            key={`${p.camera_id}-${i}`}
            center={[p.lat, p.lon]}
            radius={7}
            pathOptions={{ color: "#fff", weight: 1, fillColor: "#38bdf8", fillOpacity: 0.95 }}
          >
            <LeafletTooltip>
              #{i + 1} {p.camera_name ?? p.camera_id} · {new Date(p.at).toLocaleString("en-IN", { hour12: false })}
            </LeafletTooltip>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}
