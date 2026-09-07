import type { Camera, CameraHealth, PipelineStatus } from "./types";

export type CamState = "online" | "degraded" | "offline" | "not-started";

/** Normalised camera-health row, whichever backend shape it came from. */
export interface CamHealthRow {
  camera_id: string;
  name: string;
  city: string | null;
  capability: string | null;
  state: CamState;
  fps: number | null;
  stale_s: number | null;
  sampled_pct: number | null;
  reconnects: number;
  loop_cuts: number;
  escalated: boolean;
  codec: string | null;
  resolution: string | null;
  last_detection_at: string | null;
  last_error: string | null;
  dark: boolean | null;
  lat: number;
  lon: number;
}

/** A2's dedicated /api/cameras/health item shape (if/when live). */
interface RichHealthItem {
  camera_id: string;
  name?: string;
  city?: string | null;
  capability?: string | null;
  state: CamState;
  fps?: number | null;
  stale_s?: number | null;
  sampled_pct?: number | null;
  reconnects?: number;
  loop_cuts?: number;
  escalated?: boolean;
  codec?: string | null;
  resolution?: string | null;
  last_detection_at?: string | null;
  last_error?: string | null;
  dark?: boolean | null;
}

function deriveState(h: Partial<CameraHealth> | undefined): CamState {
  if (!h || (h.frames_seen ?? 0) === 0) return "not-started";
  if (!h.connected) return "offline";
  // `escalated` here means the scheduler gave this stream a bigger frame
  // budget under backpressure, not that it is unhealthy — a genuinely
  // connected, low-latency feed can be escalated. Judge health on
  // staleness/reconnects instead.
  if ((h.stale_s ?? 0) > 5 || (h.reconnects ?? 0) > 3) return "degraded";
  return "online";
}

/**
 * Merges the full camera roster with whichever health source is live:
 * the dedicated `/api/cameras/health` (A5, richer) when it answers, else
 * `pipeline/status.cameras` (CameraHealth[], only cameras the pipeline has
 * ever started) per the brief's documented fallback. Cameras absent from
 * both sources are "not-started".
 */
export function mergeHealth(
  cameras: Camera[],
  richHealth: RichHealthItem[] | null,
  status: PipelineStatus | null
): CamHealthRow[] {
  const rich = new Map((richHealth ?? []).map((h) => [h.camera_id, h]));
  const basic = new Map((status?.cameras ?? []).map((h) => [h.camera_id, h]));
  return cameras.map((cam) => {
    const r = rich.get(cam.id);
    const b = basic.get(cam.id);
    if (r) {
      return {
        camera_id: cam.id,
        name: r.name ?? cam.name,
        city: r.city ?? cam.city,
        capability: r.capability ?? cam.capability,
        state: r.state,
        fps: r.fps ?? null,
        stale_s: r.stale_s ?? null,
        sampled_pct: r.sampled_pct ?? null,
        reconnects: r.reconnects ?? 0,
        loop_cuts: r.loop_cuts ?? 0,
        escalated: r.escalated ?? false,
        codec: r.codec ?? cam.codec,
        resolution: r.resolution ?? (cam.width && cam.height ? `${cam.width}x${cam.height}` : null),
        last_detection_at: r.last_detection_at ?? null,
        last_error: r.last_error ?? null,
        dark: r.dark ?? null,
        lat: cam.lat,
        lon: cam.lon,
      };
    }
    return {
      camera_id: cam.id,
      name: cam.name,
      city: cam.city,
      capability: cam.capability,
      state: deriveState(b),
      fps: b?.measured_fps ?? null,
      stale_s: b?.stale_s ?? null,
      sampled_pct: null,
      reconnects: b?.reconnects ?? 0,
      loop_cuts: b?.loop_cuts ?? 0,
      escalated: b?.escalated ?? false,
      codec: cam.codec,
      resolution: cam.width && cam.height ? `${cam.width}x${cam.height}` : null,
      last_detection_at: null,
      last_error: b?.last_error ?? null,
      dark: null,
      lat: cam.lat,
      lon: cam.lon,
    };
  });
}

export const STATE_COLOUR: Record<CamState, string> = {
  online: "bg-ok",
  degraded: "bg-warn",
  offline: "bg-bad",
  "not-started": "bg-border",
};

export const STATE_TEXT: Record<CamState, string> = {
  online: "text-ok",
  degraded: "text-warn",
  offline: "text-bad",
  "not-started": "text-muted",
};
