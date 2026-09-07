"use client";
import "leaflet/dist/leaflet.css";
import { TileLayer } from "react-leaflet";

/**
 * Shared Leaflet setup for MapView, MiniMap and RouteMap.
 *
 * ponytail: CartoDB's dark tiles now render an "API KEY REQUIRED" watermark,
 * so every map uses plain OpenStreetMap tiles and gets its dark look from a
 * CSS filter on the tile pane instead (scoped to `.netra-map` so it never
 * touches anything else on the page).
 */
export const DARK_TILE_FILTER_CLASS = "netra-map";

export function DarkTiles() {
  return (
    <TileLayer
      url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      attribution="&copy; OpenStreetMap contributors"
    />
  );
}

/** Marker colour by health/capability state, shared across every map. */
export const STATE_HEX: Record<string, string> = {
  online: "#22c55e",
  ok: "#22c55e",
  degraded: "#f59e0b",
  offline: "#ef4444",
  bad: "#ef4444",
  "not-started": "#22304f",
  anpr: "#38bdf8",
  vehicle: "#8fa1c2",
};

export function stateColour(state: string | null | undefined): string {
  if (!state) return STATE_HEX["not-started"];
  return STATE_HEX[state] ?? "#8fa1c2";
}

/** Inline style block: put once per map container. Scoped to `.netra-map`. */
export function MapDarkStyle() {
  return (
    <style>{`
      .${DARK_TILE_FILTER_CLASS} .leaflet-tile-pane {
        filter: invert(1) hue-rotate(180deg) brightness(.85) contrast(.9) saturate(.6);
      }
    `}</style>
  );
}
