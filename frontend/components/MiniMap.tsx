"use client";
import "leaflet/dist/leaflet.css";
import { useRouter } from "next/navigation";
import { MapContainer, TileLayer, CircleMarker, Tooltip as LeafletTooltip } from "react-leaflet";
import type { CamHealthRow } from "@/lib/health";

const STATE_HEX: Record<string, string> = {
  online: "#22c55e",
  degraded: "#f59e0b",
  offline: "#ef4444",
  "not-started": "#22304f",
};

/** Dark-tiled overview map; click a marker to jump to the full GIS map page. */
export default function MiniMap({ cameras }: { cameras: CamHealthRow[] }) {
  const router = useRouter();
  const center: [number, number] =
    cameras.length > 0 ? [cameras[0].lat, cameras[0].lon] : [23.03, 72.56];
  return (
    <div className="h-64 overflow-hidden rounded-card border border-border">
      <MapContainer
        center={center}
        zoom={11}
        className="h-full w-full"
        scrollWheelZoom={false}
      >
        <TileLayer
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          attribution="&copy; OpenStreetMap contributors &copy; CARTO"
        />
        {cameras.map((c) => (
          <CircleMarker
            key={c.camera_id}
            center={[c.lat, c.lon]}
            radius={6}
            pathOptions={{ color: STATE_HEX[c.state], fillColor: STATE_HEX[c.state], fillOpacity: 0.9 }}
            eventHandlers={{ click: () => router.push(`/console/map?camera=${c.camera_id}`) }}
          >
            <LeafletTooltip>{c.name} · {c.state}</LeafletTooltip>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}
