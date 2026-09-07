"use client";
import { useState } from "react";
import { CameraOff } from "lucide-react";
import { apiUrl } from "@/lib/api";

/** MJPEG live tile for a single camera. Falls back to a quiet placeholder if the stream errors. */
export function CameraTile({
  cameraId,
  className,
  lazy = false,
}: {
  cameraId: string;
  className?: string;
  lazy?: boolean;
}) {
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <div
        className={
          className ??
          "flex h-64 w-full items-center justify-center rounded-card border border-border bg-surface-2 text-muted"
        }
      >
        <CameraOff aria-hidden="true" className="mr-2 h-5 w-5" />
        <span className="text-sm">Camera unavailable</span>
      </div>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- MJPEG multipart stream, not a static image
    <img
      src={apiUrl(`/api/cameras/${cameraId}/live.mjpg`)}
      alt={`Live feed from camera ${cameraId}`}
      loading={lazy ? "lazy" : "eager"}
      onError={() => setFailed(true)}
      className={className ?? "h-64 w-full rounded-card border border-border object-cover"}
    />
  );
}
