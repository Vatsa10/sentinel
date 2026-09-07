"use client";
import dynamic from "next/dynamic";
import { useQuery } from "@tanstack/react-query";
import { api, apiUrl } from "@/lib/api";
import { mergeHealth } from "@/lib/health";
import type { Camera } from "@/lib/types";

const MiniMap = dynamic(() => import("@/components/MiniMap"), {
  ssr: false,
  loading: () => <div className="h-64 w-full animate-pulse rounded-card border border-border bg-surface" />,
});

function usePreviewCameras() {
  const { data } = useQuery({
    queryKey: ["landing-cameras"],
    queryFn: () => api<Camera[]>("/api/cameras"),
    retry: 0,
  });
  return data ?? [];
}

export function LivePreview() {
  const cameras = usePreviewCameras();
  const rows = mergeHealth(cameras, null, null);
  const online = rows.find((c) => c.state === "online") ?? rows[0];

  return (
    <section className="mx-auto max-w-6xl px-6 py-16">
      <h2 className="text-center text-2xl font-bold text-text sm:text-3xl">
        This is live, not a mockup.
      </h2>
      <div className="mt-8 grid gap-6 sm:grid-cols-2">
        <div className="h-72 w-full overflow-hidden rounded-card border border-border bg-black">
          {online ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={apiUrl(`/api/cameras/${online.camera_id}/live.mjpg`)}
              alt={`Live feed: ${online.name}`}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-sm text-muted">
              Connecting…
            </div>
          )}
        </div>
        <MiniMap cameras={rows} />
      </div>
    </section>
  );
}
