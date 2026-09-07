"use client";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { toast } from "sonner";
import {
  AlertOctagon, AlertTriangle, AlertCircle, Info, Bell, Volume2, VolumeX,
  ChevronDown, ChevronUp, Camera as CameraIcon, Route as RouteIcon,
} from "lucide-react";
import { cn } from "cn";
import { api, apiUrl, ApiError } from "@/lib/api";
import { useLive } from "@/lib/live";
import type { Alert, Camera } from "@/lib/types";
import { Gate } from "@/components/Gate";
import { EmptyState } from "@/components/EmptyState";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ago } from "@/lib/time";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

/** Alerts and zone events carry a wall-clock ingest time, not scene/stream time —
 * render it plainly rather than borrowing TimeBadge's scene-time semantics. */
function RecordedAt({ at }: { at: string }) {
  return (
    <Tooltip>
      <TooltipTrigger render={<span tabIndex={0} className="mono text-xs text-muted" />}>
        {ago(at)}
      </TooltipTrigger>
      <TooltipContent>Recorded {new Date(at).toLocaleString("en-IN", { hour12: false })}</TooltipContent>
    </Tooltip>
  );
}

const SEVERITY: Record<string, { icon: typeof AlertOctagon; cls: string }> = {
  critical: { icon: AlertOctagon, cls: "text-bad bg-bad/10 border-bad/40" },
  high: { icon: AlertTriangle, cls: "text-warn bg-warn/10 border-warn/40" },
  medium: { icon: AlertCircle, cls: "text-info bg-info/10 border-info/40" },
  low: { icon: Info, cls: "text-muted bg-surface-2 border-border" },
};
const SOUND_KEY = "NETRA_ALERT_SOUND";

function beep() {
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.3);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.32);
    osc.onended = () => ctx.close();
  } catch {}
}

export default function AlertsPage() {
  const qc = useQueryClient();
  const { alerts: liveAlerts } = useLive();
  const [severity, setSeverity] = useState<string>("all");
  const [cameraId, setCameraId] = useState<string>("all");
  const [acknowledged, setAcknowledged] = useState<string>("all");
  const [plate, setPlate] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [soundOn, setSoundOn] = useState(false);
  const seenLive = useRef<Set<string>>(new Set());

  useEffect(() => {
    try { setSoundOn(localStorage.getItem(SOUND_KEY) === "1"); } catch {}
  }, []);
  const toggleSound = () => {
    setSoundOn((s) => {
      const next = !s;
      try { localStorage.setItem(SOUND_KEY, next ? "1" : "0"); } catch {}
      return next;
    });
  };

  useEffect(() => {
    for (const a of liveAlerts) {
      if (a.kind === "attributes") continue;
      const key = String(a.alert_id ?? a.id ?? `${a.camera_id}-${a.at}`);
      if (seenLive.current.has(key)) continue;
      seenLive.current.add(key);
      if (soundOn && (a.severity === "critical")) beep();
    }
  }, [liveAlerts, soundOn]);

  const { data: cameras } = useQuery({ queryKey: ["cameras"], queryFn: () => api<Camera[]>("/api/cameras") });
  const { data, isLoading } = useQuery({
    queryKey: ["alerts-page", acknowledged],
    queryFn: () => api<Alert[]>(`/api/alerts?limit=200${acknowledged !== "all" ? `&acknowledged=${acknowledged}` : ""}`),
    refetchInterval: 10000,
  });

  type Row = Alert & { isNew?: boolean };

  const merged = useMemo(() => {
    const byId = new Map<string, Row>();
    (data ?? []).forEach((a) => byId.set(String(a.id), a));
    // Live prepend: socket alerts (kind !== "attributes"/"zone") arrive ahead of the
    // next 10s poll. Dedupe by alert id; skip anything the poll already has.
    liveAlerts.forEach((m) => {
      if (m.kind === "attributes" || m.kind === "zone") return;
      if (m.alert_id == null) return;
      const id = String(m.alert_id);
      if (byId.has(id)) return;
      byId.set(id, {
        id: Number(id),
        at: (m.at as string) ?? new Date().toISOString(),
        camera_id: (m.camera_id as string) ?? "?",
        camera_name: null, lat: null, lon: null,
        score: (m.score as number) ?? 0,
        match_type: (m.match_type as string) ?? "?",
        reasons: (m.reasons as Alert["reasons"]) ?? {},
        severity: (m.severity as string) ?? "medium",
        acknowledged: false,
        plate_observed: (m.plate_observed as string) ?? (m.plate as string) ?? null,
        plate_watchlist: (m.plate_watchlist as string) ?? null,
        category: null, case_ref: null,
        evidence: (m.evidence as string) ?? null,
        detection_id: null, attributes: null,
        isNew: true,
      });
    });
    // Newest first, bounded to 200 so a burst of live events can't grow this unbounded.
    return Array.from(byId.values())
      .sort((a, b) => (a.at < b.at ? 1 : -1))
      .slice(0, 200);
  }, [data, liveAlerts]);

  const rows = useMemo(() => {
    return merged.filter((a) => {
      if (severity !== "all" && a.severity !== severity) return false;
      if (cameraId !== "all" && a.camera_id !== cameraId) return false;
      if (plate) {
        const p = plate.trim().toUpperCase();
        if (!(a.plate_observed ?? "").toUpperCase().includes(p) && !(a.plate_watchlist ?? "").toUpperCase().includes(p)) return false;
      }
      return true;
    });
  }, [merged, severity, cameraId, plate]);

  const ack = useMutation({
    mutationFn: (id: number) => api(`/api/alerts/${id}/acknowledge`, { method: "POST" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["alerts-page"] }); qc.invalidateQueries({ queryKey: ["alerts"] }); },
    onError: (e: unknown) => { if (e instanceof ApiError) toast.error(e.message); },
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Alerts</h1>
        <Button size="sm" variant="secondary" onClick={toggleSound} aria-pressed={soundOn}>
          {soundOn ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
          Sound {soundOn ? "on" : "off"}
        </Button>
      </div>

      <div className="flex flex-wrap gap-2">
        <Select value={severity} onValueChange={(v) => setSeverity(String(v))}>
          <SelectTrigger className="w-40"><SelectValue placeholder="Severity" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All severities</SelectItem>
            <SelectItem value="critical">Critical</SelectItem>
            <SelectItem value="high">High</SelectItem>
            <SelectItem value="medium">Medium</SelectItem>
            <SelectItem value="low">Low</SelectItem>
          </SelectContent>
        </Select>
        <Select value={cameraId} onValueChange={(v) => setCameraId(String(v))}>
          <SelectTrigger className="w-44"><SelectValue placeholder="Camera" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All cameras</SelectItem>
            {(cameras ?? []).map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={acknowledged} onValueChange={(v) => setAcknowledged(String(v))}>
          <SelectTrigger className="w-40"><SelectValue placeholder="Status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="false">Unacknowledged</SelectItem>
            <SelectItem value="true">Acknowledged</SelectItem>
          </SelectContent>
        </Select>
        <Input placeholder="Filter by plate…" value={plate} onChange={(e) => setPlate(e.target.value)} className="mono w-44" />
      </div>

      {!isLoading && rows.length === 0 ? (
        <EmptyState icon={Bell} title="No alerts" body="No alerts match these filters yet." />
      ) : (
        <div className="overflow-x-auto rounded-card border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead />
                <TableHead>Severity</TableHead>
                <TableHead>Plate</TableHead>
                <TableHead>Camera</TableHead>
                <TableHead>Time</TableHead>
                <TableHead>Score</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((a) => {
                const sev = SEVERITY[a.severity] ?? SEVERITY.medium;
                const Icon = sev.icon;
                const isOpen = expanded === String(a.id);
                return (
                  <Fragment key={a.id}>
                    <TableRow
                      className={cn("cursor-pointer hover:bg-surface-2", a.isNew && "animate-in fade-in slide-in-from-top-2 duration-200")}
                      onClick={() => setExpanded(isOpen ? null : String(a.id))}
                    >
                      <TableCell>{isOpen ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}</TableCell>
                      <TableCell>
                        <span className={cn("mono inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs", sev.cls)}>
                          <Icon className="size-3.5" />{a.severity}
                        </span>
                      </TableCell>
                      <TableCell className="mono">{a.plate_observed ?? a.plate_watchlist ?? "—"}</TableCell>
                      <TableCell className="mono">{a.camera_name ?? a.camera_id}</TableCell>
                      <TableCell><RecordedAt at={a.at} /></TableCell>
                      <TableCell className="mono">{(a.score * 100).toFixed(0)}%</TableCell>
                      <TableCell>{a.acknowledged ? "Acknowledged" : "Open"}</TableCell>
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <div className="flex gap-1">
                          <Gate min="operator" reason="Sign in as operator or admin to acknowledge alerts">
                            <Button size="sm" variant="secondary" disabled={a.acknowledged} onClick={() => ack.mutate(a.id)}>
                              {a.acknowledged ? "Acked" : "Acknowledge"}
                            </Button>
                          </Gate>
                          <Button size="sm" variant="ghost" render={<Link href={`/console/wall?camera=${a.camera_id}`} />}>
                            <CameraIcon className="size-3.5" /> Camera
                          </Button>
                          {(a.plate_observed ?? a.plate_watchlist) && (
                            <Button size="sm" variant="ghost" render={<Link href={`/console/vehicles?plate=${a.plate_observed ?? a.plate_watchlist}`} />}>
                              <RouteIcon className="size-3.5" /> Trace
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                    {isOpen && (
                      <TableRow>
                        <TableCell colSpan={8}>
                          <div className="flex flex-wrap gap-4 p-2">
                            {a.evidence && (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img src={apiUrl("/evidence/" + a.evidence.replace(/^\/?evidence\//, ""))} alt="Evidence" className="h-32 w-44 rounded-card border border-border object-cover" />
                            )}
                            <div className="flex flex-col gap-1 text-sm">
                              <div><span className="text-muted">Match type: </span>{a.match_type}</div>
                              <div><span className="text-muted">Category: </span>{a.category ?? "—"}</div>
                              <div><span className="text-muted">Case ref: </span>{a.case_ref ?? "—"}</div>
                              <div><span className="text-muted">Watchlist plate: </span><span className="mono">{a.plate_watchlist ?? "—"}</span></div>
                            </div>
                            <div className="flex flex-col gap-1 text-sm">
                              <div className="text-muted">Score breakdown</div>
                              {Object.entries(a.reasons ?? {}).map(([k, r]) => r && (
                                <div key={k} className="flex gap-2">
                                  <span className="mono w-24 capitalize">{k}</span>
                                  <span className="mono">{(r.score * 100).toFixed(0)}%</span>
                                  <span className="text-muted">{r.detail}</span>
                                </div>
                              ))}
                              {Object.keys(a.reasons ?? {}).length === 0 && <span className="text-muted">No breakdown available</span>}
                            </div>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
