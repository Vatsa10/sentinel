"use client";
import dynamic from "next/dynamic";

const LeafletBase = dynamic(
  () => import("@/components/map/leafletBase").then((m) => m.LeafletBase),
  { ssr: false, loading: () => <div className="h-full w-full animate-pulse bg-surface-2" /> }
);

/** Small non-interactive Gujarat map, used to give the live preview section spatial context. */
export function MiniMap({ className }: { className?: string }) {
  return (
    <div className={className ?? "h-64 w-full overflow-hidden rounded-card border border-border"}>
      <LeafletBase />
    </div>
  );
}
