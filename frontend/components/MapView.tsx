"use client";
import { useMemo } from "react";
import { MapContainer, CircleMarker, Circle, Tooltip as LeafletTooltip, useMap } from "react-leaflet";
import type { Camera } from "@/lib/types";
import type { CamHealthRow } from "@/lib/health";
import { DarkTiles, MapDarkStyle, DARK_TILE_FILTER_CLASS, stateColour } from "@/components/map/leafletBase";

export interface GapZone {
  id: string;
  name: string;
  lat: number;
  lon: number;
  reason: string;
}

/** Focuses the map on a camera when `focusId` changes. */
function Focus({ cameras, focusId }: { cameras: Camera[]; focusId: string | null }) {
  const map = useMap();
  useMemo(() => {
    if (!focusId) return;
    const cam = cameras.find((c) => c.id === focusId);
    if (cam) map.setView([cam.lat, cam.lon], 14, { animate: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId]);
  return null;
}

export default function MapView({
  cameras,
  health,
  gapZones,
  layers,
  focusId,
  onSelect,
}: {
  cameras: Camera[];
  health: Map<string, CamHealthRow>;
  gapZones: GapZone[];
  layers: { coverage: boolean; gaps: boolean; state: boolean };
  focusId: string | null;
  onSelect: (id: string) => void;
}) {
  const center: [number, number] =
    cameras.length > 0 ? [cameras[0].lat, cameras[0].lon] : [22.5, 71.5];

  return (
    <div className={`h-full w-full ${DARK_TILE_FILTER_CLASS}`}>
      <MapDarkStyle />
      <MapContainer center={center} zoom={7} className="h-full w-full" keyboard>
        <DarkTiles />
        <Focus cameras={cameras} focusId={focusId} />
        {layers.coverage &&
          cameras.map((c) => (
            <Circle
              key={`cov-${c.id}`}
              center={[c.lat, c.lon]}
              radius={150}
              pathOptions={{ color: "#FF6B00", fillColor: "#FF6B00", fillOpacity: 0.1, weight: 1 }}
            />
          ))}
        {layers.gaps &&
          gapZones.map((g) => (
            <Circle
              key={`gap-${g.id}`}
              center={[g.lat, g.lon]}
              radius={400}
              pathOptions={{
                color: "#ef4444",
                fillColor: "#ef4444",
                fillOpacity: 0.05,
                weight: 1.5,
                dashArray: "6 4",
              }}
            >
              <LeafletTooltip>{g.name}: {g.reason}</LeafletTooltip>
            </Circle>
          ))}
        {cameras.map((c) => {
          const h = health.get(c.id);
          const state = layers.state ? (h?.state ?? "not-started") : c.capability;
          return (
            <CircleMarker
              key={c.id}
              center={[c.lat, c.lon]}
              radius={7}
              pathOptions={{ color: "#fff", weight: 1, fillColor: stateColour(state), fillOpacity: 0.95 }}
              eventHandlers={{
                click: () => onSelect(c.id),
                add: (e) => {
                  // ponytail: CircleMarker has no `keyboard`/`title` props in
                  // react-leaflet's types (Marker-only); set them on the
                  // rendered <path> directly so markers stay tab-focusable.
                  const el = e.target.getElement?.() as SVGElement | undefined;
                  if (el) {
                    el.setAttribute("tabindex", "0");
                    el.setAttribute("role", "button");
                    const titleEl = document.createElementNS("http://www.w3.org/2000/svg", "title");
                    titleEl.textContent = `${c.name} (${c.id})`;
                    el.appendChild(titleEl);
                    el.addEventListener("keydown", (ke) => {
                      if ((ke as KeyboardEvent).key === "Enter") onSelect(c.id);
                    });
                  }
                },
              }}
            >
              <LeafletTooltip>
                {c.name} · {c.city ?? "unknown city"} · {h?.state ?? "not-started"}
              </LeafletTooltip>
            </CircleMarker>
          );
        })}
      </MapContainer>
    </div>
  );
}
