"use client";
import { useState } from "react";
import { toast } from "sonner";
import { UploadCloud } from "lucide-react";
import { api, apiBase, ApiError } from "@/lib/api";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";

interface RowResult { row: number; ok: boolean; detail: string; }

export function OnboardDrawer({ open, onOpenChange, onDone }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onDone: () => void;
}) {
  const [probe, setProbe] = useState(true);
  const [busy, setBusy] = useState(false);

  // Own-feed / manual single-camera form.
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [city, setCity] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [capability, setCapability] = useState("anpr");

  const [csvResults, setCsvResults] = useState<RowResult[] | null>(null);
  const [csvBusy, setCsvBusy] = useState(false);

  async function reOnboard() {
    setBusy(true);
    try {
      const r = await api<{ onboarded: number }>(`/api/cameras/onboard?probe=${probe}`, { method: "POST" });
      toast.success(`Re-onboarded ${r.onboarded} cameras from the registry catalogue.`);
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Onboard failed");
    } finally {
      setBusy(false);
    }
  }

  async function submitOwnFeed() {
    if (!path) { toast.error("A file path is required"); return; }
    setBusy(true);
    try {
      const r = await api<{ camera_id: string }>("/api/cameras/own-feed", {
        method: "POST",
        json: {
          path, name: name || undefined, camera_id: cameraId || undefined,
          city: city || undefined,
          lat: lat ? Number(lat) : undefined, lon: lon ? Number(lon) : undefined,
          capability,
        },
      });
      toast.success(`Camera ${r.camera_id} registered.`);
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Own-feed registration failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCsvFile(file: File) {
    const text = await file.text();
    const lines = text.trim().split(/\r?\n/);
    const header = lines[0].split(",").map((h) => h.trim());
    setCsvBusy(true);
    const results: RowResult[] = [];
    for (let i = 1; i < lines.length; i++) {
      if (!lines[i].trim()) continue;
      const cells = lines[i].split(",").map((c) => c.trim());
      const row: Record<string, string> = {};
      header.forEach((h, idx) => { row[h] = cells[idx] ?? ""; });
      try {
        const r = await api<{ camera_id: string }>("/api/cameras/own-feed", {
          method: "POST",
          json: {
            path: row.path, name: row.name || undefined, camera_id: row.camera_id || undefined,
            city: row.city || undefined,
            lat: row.lat ? Number(row.lat) : undefined, lon: row.lon ? Number(row.lon) : undefined,
            capability: row.capability || "anpr",
          },
        });
        results.push({ row: i, ok: true, detail: `registered as ${r.camera_id}` });
      } catch (e) {
        results.push({ row: i, ok: false, detail: e instanceof ApiError ? e.message : "failed" });
      }
      setCsvResults([...results]);
    }
    setCsvBusy(false);
    onDone();
  }

  const curl = `curl -X POST "${apiBase()}/api/cameras/onboard?probe=true" -H "X-API-Key: <your key>"`;
  const curlOwn = `curl -X POST "${apiBase()}/api/cameras/own-feed" -H "X-API-Key: <your key>" -H "Content-Type: application/json" -d '{"path":"C:/videos/mine.mp4","name":"My test feed","capability":"anpr"}'`;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto p-4 sm:max-w-lg">
        <SheetHeader className="p-0">
          <SheetTitle>Onboard a camera</SheetTitle>
        </SheetHeader>

        <Tabs defaultValue="manual">
          <TabsList>
            <TabsTrigger value="manual">Manual</TabsTrigger>
            <TabsTrigger value="csv">CSV bulk</TabsTrigger>
            <TabsTrigger value="api">API</TabsTrigger>
            <TabsTrigger value="own">Own feed</TabsTrigger>
          </TabsList>

          <TabsContent value="manual" className="flex flex-col gap-3 pt-3">
            <p className="text-xs text-muted">
              {/* ponytail: the registry-onboard route (app.py:141) only re-runs the
                 Government catalogue fetch/probe; it has no per-camera manual
                 fields. Per-camera manual entry is what the Own feed tab does. */}
              Re-runs registry onboarding: fetches the Government camera catalogue,
              probes each feed and persists what it finds. There is no per-camera
              manual form on this route — use &quot;Own feed&quot; to add one camera by hand.
            </p>
            <div className="flex items-center gap-2">
              <Switch checked={probe} onCheckedChange={setProbe} id="probe" />
              <Label htmlFor="probe">Probe each feed (slower, measures codec/health)</Label>
            </div>
            <Button onClick={reOnboard} disabled={busy}>Re-run onboarding</Button>
          </TabsContent>

          <TabsContent value="csv" className="flex flex-col gap-3 pt-3">
            <p className="text-xs text-muted">
              Columns: path, name, camera_id, city, lat, lon, capability. Each row
              is posted to <span className="mono">/api/cameras/own-feed</span> one
              at a time so you can watch progress.
            </p>
            <input
              type="file"
              accept=".csv"
              disabled={csvBusy}
              onChange={(e) => e.target.files?.[0] && onCsvFile(e.target.files[0])}
              className="text-xs"
            />
            {csvResults && (
              <ul className="mono max-h-48 overflow-y-auto text-xs">
                {csvResults.map((r) => (
                  <li key={r.row} className={r.ok ? "text-ok" : "text-bad"}>
                    row {r.row}: {r.detail}
                  </li>
                ))}
              </ul>
            )}
          </TabsContent>

          <TabsContent value="api" className="flex flex-col gap-3 pt-3">
            <p className="text-xs text-muted">Equivalent calls against the current API base.</p>
            <pre className="mono overflow-x-auto rounded-ctl border border-border bg-surface-2 p-2 text-xs">{curl}</pre>
            <pre className="mono overflow-x-auto rounded-ctl border border-border bg-surface-2 p-2 text-xs">{curlOwn}</pre>
          </TabsContent>

          <TabsContent value="own" className="flex flex-col gap-3 pt-3">
            <Field label="File path"><Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:/videos/mine.mp4" /></Field>
            <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My test feed" /></Field>
            <Field label="Camera id (optional)"><Input value={cameraId} onChange={(e) => setCameraId(e.target.value)} /></Field>
            <Field label="City (optional)"><Input value={city} onChange={(e) => setCity(e.target.value)} /></Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Lat (optional)"><Input value={lat} onChange={(e) => setLat(e.target.value)} /></Field>
              <Field label="Lon (optional)"><Input value={lon} onChange={(e) => setLon(e.target.value)} /></Field>
            </div>
            <Field label="Capability">
              <Input value={capability} onChange={(e) => setCapability(e.target.value)} placeholder="anpr" />
            </Field>
            <Button onClick={submitOwnFeed} disabled={busy}>
              <UploadCloud className="size-4" /> Register feed
            </Button>
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <Label>{label}</Label>
      {children}
    </div>
  );
}
