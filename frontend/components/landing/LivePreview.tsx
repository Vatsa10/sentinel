"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { CameraTile } from "@/components/CameraTile";
import { MiniMap } from "@/components/MiniMap";

type CameraHealth = { id: string; state: "online" | "degraded" | "offline" | "not-started" };

function usePreviewCamera() {
  const { data } = useQuery({
    queryKey: ["landing-camera-health"],
    queryFn: () => api<CameraHealth[]>("/api/cameras/health"),
    retry: 0,
  });
  const online = data?.find((c) => c.state === "online");
  return online?.id ?? "cam13";
}

export function LivePreview() {
  const cameraId = usePreviewCamera();

  return (
    <section className="mx-auto max-w-6xl px-6 py-16">
      <h2 className="text-center text-2xl font-bold text-text sm:text-3xl">
        This is live, not a mockup.
      </h2>
      <div className="mt-8 grid gap-6 sm:grid-cols-2">
        <CameraTile cameraId={cameraId} className="h-72 w-full rounded-card border border-border object-cover" />
        <MiniMap className="h-72 w-full overflow-hidden rounded-card border border-border" />
      </div>
    </section>
  );
}
