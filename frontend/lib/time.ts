export function fmtTime(d: { scene_time?: string | null; scene_time_corroborated?: boolean | null; pts_ms: number; wall_time?: string }) {
  if (d.scene_time && d.scene_time_corroborated) {
    return { label: new Date(d.scene_time).toLocaleString("en-IN", { hour12: false }), basis: "scene" as const };
  }
  const s = d.pts_ms / 1000, h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return { label: `T+${h ? h + ":" : ""}${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`, basis: "stream" as const };
}
export const ago = (iso: string) => { const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? `${s | 0}s ago` : s < 3600 ? `${(s / 60) | 0}m ago` : `${(s / 3600) | 0}h ago`; };
