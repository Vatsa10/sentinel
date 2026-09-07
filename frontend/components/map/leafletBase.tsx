"use client";
import { MapContainer, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";

/** Gujarat-centred dark OSM basemap, shared by MiniMap and the console map views. */
const GUJARAT_CENTER: [number, number] = [22.6, 71.6];

export function LeafletBase({
  children,
  center = GUJARAT_CENTER,
  zoom = 6,
  className,
}: {
  children?: React.ReactNode;
  center?: [number, number];
  zoom?: number;
  className?: string;
}) {
  return (
    <MapContainer
      center={center}
      zoom={zoom}
      scrollWheelZoom={false}
      dragging={false}
      zoomControl={false}
      attributionControl={false}
      className={className ?? "h-full w-full"}
      style={{ background: "#0e1830" }}
    >
      <TileLayer
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        // ponytail: CARTO dark basemap tiles — no API key required, matches the ops-room theme.
      />
      {children}
    </MapContainer>
  );
}
