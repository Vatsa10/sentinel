"use client";
import { useRouter } from "next/navigation";
import { MapContainer, CircleMarker, Tooltip as LeafletTooltip } from "react-leaflet";
import type { CamHealthRow } from "@/lib/health";
import { DarkTiles, MapDarkStyle, DARK_TILE_FILTER_CLASS, stateColour } from "@/components/map/leafletBase";

/** Dark-tiled overview map; click a marker to jump to the full GIS map page. */
export default function MiniMap({ cameras }: { cameras: CamHealthRow[] }) {
  const router = useRouter();
  const center: [number, number] =
    cameras.length > 0 ? [cameras[0].lat, cameras[0].lon] : [23.03, 72.56];
  return (
    <div className={`h-64 overflow-hidden rounded-card border border-border ${DARK_TILE_FILTER_CLASS}`}>
      <MapDarkStyle />
      <MapContainer
        center={center}
        zoom={11}
        className="h-full w-full"
        scrollWheelZoom={false}
      >
        <DarkTiles />
        {cameras.map((c) => (
          <CircleMarker
            key={c.camera_id}
            center={[c.lat, c.lon]}
            radius={6}
            pathOptions={{ color: stateColour(c.state), fillColor: stateColour(c.state), fillOpacity: 0.9 }}
            eventHandlers={{ click: () => router.push(`/console/map?camera=${c.camera_id}`) }}
          >
            <LeafletTooltip>{c.name} · {c.state}</LeafletTooltip>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}
