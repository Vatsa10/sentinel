"use client";
import { useEffect, useRef, useState } from "react";
import { ScanEye, Film, Camera as CameraIcon, Maximize2, X, Circle } from "lucide-react";
import { cn } from "cn";
import { api, apiUrl, ApiError } from "@/lib/api";
import type { Camera } from "@/lib/types";
import type { CamHealthRow } from "@/lib/health";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { HlsPlayer } from "@/components/HlsPlayer";

type Mode = "live" | "smooth" | "still";

/**
 * One video-wall tile: MJPEG "AI live" overlay feed by default, on-demand
 * "Smooth" HLS (relay-limited to 4 concurrent, keeps its own hls/status
 * poll alive every 20s per the brief), or a still snapshot.
 */
export function CameraTile({
  cam,
  health,
  onRemove,
}: {
  cam: Camera;
  health?: CamHealthRow;
  onRemove: () => void;
}) {
  const [mode, setMode] = useState<Mode>("live");
  const [hlsUrl, setHlsUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [bust, setBust] = useState(Date.now());
  const ref = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    if (mode !== "smooth") {
      setHlsUrl(null);
      return;
    }
    let alive = true;
    let poll: ReturnType<typeof setInterval> | undefined;
    api<{ ready: boolean; url: string }>(`/api/cameras/${cam.id}/hls/start`, { method: "POST" })
      .then(async () => {
        for (let i = 0; i < 40 && alive; i++) {
          const st = await api<{ ready: boolean; url: string }>(`/api/cameras/${cam.id}/hls/status`);
          if (st.ready) {
            setHlsUrl(apiUrl(st.url));
            break;
          }
          await new Promise((r) => setTimeout(r, 500));
        }
        // keeps the relay alive
        poll = setInterval(() => api(`/api/cameras/${cam.id}/hls/status`).catch(() => {}), 20000);
      })
      .catch((e: ApiError) => {
        setErr(e.status === 429 ? "4 smooth streams max. Close one first." : e.message);
        setMode("live");
      });
    return () => {
      alive = false;
      if (poll) clearInterval(poll);
      api(`/api/cameras/${cam.id}/hls/stop`, { method: "POST" }).catch(() => {});
    };
  }, [mode, cam.id]);

  // MJPEG closes on unmount because the <img> is removed from the DOM; also
  // clear its src whenever `mode` changes away from an <img>-rendering mode
  // (live/still) — e.g. switching to "smooth" — so the open connection is
  // forced closed immediately rather than waiting on the DOM diff/GC.
  useEffect(() => {
    const img = imgRef.current;
    return () => {
      if (img) img.src = "";
    };
  }, [mode]);

  const src =
    mode === "live"
      ? apiUrl(`/api/cameras/${cam.id}/live.mjpg?t=${bust}`)
      : apiUrl(`/api/cameras/${cam.id}/snapshot?t=${bust}`);

  return (
    <div
      ref={ref}
      className="group relative aspect-video overflow-hidden rounded-[10px] border border-border bg-black"
    >
      {mode === "smooth" && hlsUrl ? (
        <HlsPlayer src={hlsUrl} />
      ) : mode === "smooth" ? (
        <Skeleton className="h-full w-full" />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element -- MJPEG multipart stream, not a static asset
        <img
          ref={imgRef}
          src={src}
          alt={`Live view of ${cam.name}`}
          className="h-full w-full object-contain"
          onError={() => setTimeout(() => setBust(Date.now()), 2000)}
        />
      )}
      <header className="absolute inset-x-0 top-0 flex items-center gap-2 bg-gradient-to-b from-black/70 to-transparent px-2 py-1 text-xs text-white">
        <span className="mono">{cam.id}</span>
        <span className="truncate">{cam.name}</span>
        {health && (
          <span
            className={cn(
              "ml-auto inline-flex items-center gap-1",
              health.state === "online"
                ? "text-ok"
                : health.state === "degraded"
                  ? "text-warn"
                  : "text-bad"
            )}
          >
            <Circle className="size-2 fill-current" />
            {health.state}
            {health.fps ? ` · ${health.fps.toFixed(1)} fps` : ""}
          </span>
        )}
        {cam.codec && <span className="mono opacity-70">{cam.codec}</span>}
      </header>
      <footer className="absolute inset-x-0 bottom-0 flex gap-1 bg-gradient-to-t from-black/70 p-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
        <Button
          size="sm"
          className="h-11 sm:h-7"
          variant={mode === "live" ? "default" : "secondary"}
          onClick={() => setMode("live")}
        >
          <ScanEye className="size-4" />
          AI live
        </Button>
        <Button
          size="sm"
          className="h-11 sm:h-7"
          variant={mode === "smooth" ? "default" : "secondary"}
          onClick={() => setMode("smooth")}
        >
          <Film className="size-4" />
          Smooth
        </Button>
        <Button
          size="sm"
          className="h-11 sm:h-7"
          variant="secondary"
          onClick={() => setMode("still")}
        >
          <CameraIcon className="size-4" />
          Still
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto h-11 w-11 sm:h-7 sm:w-7"
          aria-label="Fullscreen"
          onClick={() => ref.current?.requestFullscreen()}
        >
          <Maximize2 className="size-4" />
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-11 w-11 sm:h-7 sm:w-7"
          aria-label="Remove tile"
          onClick={onRemove}
        >
          <X className="size-4" />
        </Button>
      </footer>
      {err && (
        <div role="alert" className="absolute inset-x-2 bottom-12 rounded bg-bad/90 px-2 py-1 text-xs text-white">
          {err}
        </div>
      )}
    </div>
  );
}
