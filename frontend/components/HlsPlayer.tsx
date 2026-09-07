"use client";
import { useEffect, useRef } from "react";
import Hls from "hls.js";

/** Plays an HLS stream: `hls.js` where supported, native `<video src>` on Safari. */
export function HlsPlayer({ src }: { src: string }) {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    let hls: Hls | null = null;
    if (Hls.isSupported()) {
      hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 3 });
      hls.loadSource(src);
      hls.attachMedia(video);
    } else {
      video.src = src;
    }
    return () => {
      hls?.destroy();
    };
  }, [src]);

  return (
    <video
      ref={ref}
      autoPlay
      muted
      playsInline
      controls
      className="h-full w-full object-contain"
    />
  );
}
