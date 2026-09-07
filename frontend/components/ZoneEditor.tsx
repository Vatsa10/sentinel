"use client";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { RotateCcw, Trash2 } from "lucide-react";
import { cn } from "cn";
import { api, apiUrl, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Camera, Zone } from "@/lib/types";
import { Gate } from "@/components/Gate";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

/** The three real backend rule values (netra/analytics/zones.py RULE_TYPES = intrusion,
 * crossing, loitering). There is no separate direction-aware "wrong-way" rule — a
 * crossing line counts traffic in both directions, so it's labelled honestly here
 * rather than implying a distinction the backend doesn't make. */
const KIND_OPTIONS: { value: string; label: string; needsLine?: boolean; params?: "dwell" | "none" }[] = [
  { value: "intrusion", label: "Intrusion (restricted area)", params: "none" },
  { value: "loitering", label: "Loitering (dwell)", params: "dwell" },
  { value: "crossing", label: "Line crossing (count)", needsLine: true, params: "none" },
];

type Point = [number, number];

export function ZoneEditor({ cameras }: { cameras: Camera[] }) {
  const qc = useQueryClient();
  const [cameraId, setCameraId] = useState<string>(cameras[0]?.id ?? "");
  const [kindOpt, setKindOpt] = useState(KIND_OPTIONS[0].value);
  const [name, setName] = useState("Zone");
  const [dwellS, setDwellS] = useState("30");
  const [points, setPoints] = useState<Point[]>([]);
  const [selectedZone, setSelectedZone] = useState<Zone | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  const { can } = useAuth();
  const isAdmin = can("admin");
  const opt = KIND_OPTIONS.find((k) => k.value === kindOpt) ?? KIND_OPTIONS[0];
  const backendRule = opt.value;
  const needed = opt.needsLine ? 2 : 3;
  const [closed, setClosed] = useState(false);

  const { data: zones } = useQuery({
    queryKey: ["zones", cameraId],
    queryFn: () => api<Zone[]>(`/api/zones?camera_id=${encodeURIComponent(cameraId)}`),
    enabled: !!cameraId,
  });

  const [snapVersion, setSnapVersion] = useState(0);
  const snapshotUrl = cameraId ? apiUrl(`/api/cameras/${cameraId}/snapshot?refresh=true&v=${snapVersion}`) : "";

  const draw = () => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (img && img.complete && img.naturalWidth) {
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    } else {
      ctx.fillStyle = "#111";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    // existing zones, muted
    (zones ?? []).forEach((z) => {
      if (selectedZone?.id === z.id) return;
      ctx.strokeStyle = "rgba(148,163,184,0.7)";
      ctx.fillStyle = "rgba(148,163,184,0.12)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      z.points.forEach(([x, y], i) => {
        const px = x * canvas.width, py = y * canvas.height;
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      });
      if (z.rule !== "crossing") ctx.closePath();
      if (z.rule !== "crossing") ctx.fill();
      ctx.stroke();
      const [lx, ly] = z.points[0];
      ctx.fillStyle = "#e2e8f0";
      ctx.font = "12px sans-serif";
      ctx.fillText(z.name, lx * canvas.width + 4, ly * canvas.height - 4);
    });
    // selected zone highlighted
    if (selectedZone) {
      ctx.strokeStyle = "#f97316";
      ctx.fillStyle = "rgba(249,115,22,0.15)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      selectedZone.points.forEach(([x, y], i) => {
        const px = x * canvas.width, py = y * canvas.height;
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      });
      if (selectedZone.rule !== "crossing") { ctx.closePath(); ctx.fill(); }
      ctx.stroke();
    }
    // in-progress polygon, accent
    if (points.length) {
      ctx.strokeStyle = "#38bdf8";
      ctx.fillStyle = "rgba(56,189,248,0.15)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      points.forEach(([x, y], i) => {
        const px = x * canvas.width, py = y * canvas.height;
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      });
      ctx.stroke();
      if (points.length >= 3 && !opt.needsLine) { ctx.closePath(); ctx.fill(); }
      points.forEach(([x, y]) => {
        ctx.beginPath();
        ctx.arc(x * canvas.width, y * canvas.height, 4, 0, Math.PI * 2);
        ctx.fillStyle = "#38bdf8";
        ctx.fill();
      });
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { draw(); }, [points, zones, selectedZone, snapVersion]);

  useEffect(() => {
    setPoints([]);
    setSelectedZone(null);
    setClosed(false);
  }, [cameraId]);

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isAdmin) return; // drawing is an admin-only affordance; see the Gate below
    const canvas = canvasRef.current;
    if (!canvas || closed) return;
    if (opt.needsLine && points.length >= 2) return;
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    setPoints((p) => [...p, [x, y]]);
  };

  /** Double-click / Enter only marks the polygon complete — it never saves.
   * Saving is exclusively the gated Save button below, per the admin-only write path. */
  const closePolygon = () => {
    if (!isAdmin || points.length < needed) return;
    setClosed(true);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setPoints([]); setClosed(false); }
      if (e.key === "Backspace") { setClosed(false); setPoints((p) => p.slice(0, -1)); }
      if (e.key === "Enter") closePolygon();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points.length, needed, isAdmin, closed]);

  const save = useMutation({
    mutationFn: () =>
      api("/api/zones", {
        method: "POST",
        json: {
          camera_id: cameraId,
          name,
          rule: backendRule,
          points,
          classes: [],
          severity: "medium",
          dwell_s: opt.params === "dwell" ? Number(dwellS) || 30 : 30,
        },
      }),
    onSuccess: () => {
      toast.success("Zone saved");
      setPoints([]);
      setClosed(false);
      qc.invalidateQueries({ queryKey: ["zones", cameraId] });
    },
    onError: (e: unknown) => { if (e instanceof ApiError) toast.error(e.message); },
  });

  const del = useMutation({
    mutationFn: (id: number) => api(`/api/zones/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast.success("Zone deleted"); setSelectedZone(null); qc.invalidateQueries({ queryKey: ["zones", cameraId] }); },
    onError: (e: unknown) => { if (e instanceof ApiError) toast.error(e.message); },
  });

  const pickZone = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas || !zones) return;
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    const hit = zones.find((z) => pointInPoly(x, y, z.points));
    setSelectedZone(hit ?? null);
  };

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      <div className="flex flex-col gap-2">
        <div ref={wrapRef} className="relative aspect-video w-full max-w-2xl overflow-hidden rounded-card border border-border bg-surface-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            ref={imgRef}
            src={snapshotUrl}
            alt=""
            className="hidden"
            onLoad={() => setSnapVersion((v) => v)}
          />
          <Tooltip>
            <TooltipTrigger
              render={
                <canvas
                  ref={canvasRef}
                  width={960}
                  height={540}
                  className={cn("h-full w-full", isAdmin ? "cursor-crosshair" : "cursor-not-allowed")}
                  onClick={(e) => { handleClick(e); pickZone(e); }}
                  onDoubleClick={closePolygon}
                  tabIndex={0}
                />
              }
            />
            {!isAdmin && <TooltipContent>Sign in as admin to edit zones</TooltipContent>}
          </Tooltip>
        </div>
        <div className="flex gap-2 text-xs text-muted">
          <span>Click to add points</span>·<span>Double-click/Enter to close the polygon</span>·
          <span>Backspace removes last point</span>·<span>Escape cancels</span>
          {closed && <span className="text-accent">· Polygon closed — press Save to write it</span>}
        </div>
        <Button size="sm" variant="secondary" onClick={() => setSnapVersion((v) => v + 1)}>
          <RotateCcw className="size-4" /> Refresh snapshot
        </Button>
      </div>

      <div className="flex w-full max-w-xs flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <Label>Camera</Label>
          <Select value={cameraId} onValueChange={(v) => setCameraId(String(v))}>
            <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>{cameras.map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="zone-name">Name</Label>
          <Input id="zone-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Kind</Label>
          <Select value={kindOpt} onValueChange={(v) => { setKindOpt(String(v)); setPoints([]); setClosed(false); }}>
            <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>{KIND_OPTIONS.map((k) => <SelectItem key={k.value} value={k.value}>{k.label}</SelectItem>)}</SelectContent>
          </Select>
          <p className="text-xs text-muted">
            Direction-aware wrong-way detection is not implemented; line crossing counts both directions.
          </p>
        </div>
        {opt.params === "dwell" && (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="zone-dwell">Dwell seconds</Label>
            <Input id="zone-dwell" type="number" value={dwellS} onChange={(e) => setDwellS(e.target.value)} />
          </div>
        )}
        <div className="text-xs text-muted">
          {points.length}/{needed}+ points placed
          {opt.needsLine && points.length < 2 ? " (line needs exactly 2)" : ""}
        </div>
        <Gate min="admin" reason="Sign in as admin to save zone rules">
          <Button disabled={points.length < needed || save.isPending} onClick={() => save.mutate()}>
            Save zone
          </Button>
        </Gate>
        {!isAdmin && <p className="text-xs text-muted">Sign in as admin to edit zones</p>}

        {selectedZone && (
          <div className="flex flex-col gap-2 rounded-card border border-border p-3">
            <div className="text-sm font-medium">{selectedZone.name}</div>
            <div className="text-xs text-muted">{selectedZone.rule}</div>
            <Gate min="admin" reason="Sign in as admin to delete zone rules">
              <Button size="sm" variant="destructive" onClick={() => del.mutate(selectedZone.id)}>
                <Trash2 className="size-4" /> Delete
              </Button>
            </Gate>
          </div>
        )}
      </div>
    </div>
  );
}

function pointInPoly(x: number, y: number, polygon: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i], [xj, yj] = polygon[j];
    const intersect = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi || 1e-12) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}
