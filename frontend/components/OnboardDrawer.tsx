"use client";
import { useState } from "react";
import { toast } from "sonner";
import { UploadCloud, FileUp } from "lucide-react";
import { api, apiBase, ApiError } from "@/lib/api";
import type { Camera } from "@/lib/types";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const ID_RE = /^[A-Za-z0-9_-]{2,32}$/;
const URL_SCHEMES = ["rtsp://", "rtsps://", "http://", "https://"];
const KNOWN_DEPARTMENTS = ["Home Department", "Traffic Police", "Municipal Corporation", "Highways"];
const CSV_COLUMNS = ["id", "name", "department", "city", "district", "lat", "lon", "hls_url", "rtsp_url", "capability"];

interface BulkResult { created: number; updated: number; errors: { row: number; id: string | null; detail: string }[] }

/**
 * Small RFC-4180-ish CSV parser: handles double-quoted fields (including
 * embedded commas and newlines) and escaped quotes (""), which a naive
 * `line.split(",")` breaks on. Blank trailing lines are dropped.
 */
function parseCsvTable(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field); field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some((f) => f.trim() !== "")) rows.push(row);
      row = [];
    } else {
      field += c;
    }
  }
  if (field !== "" || row.length > 0) { row.push(field); if (row.some((f) => f.trim() !== "")) rows.push(row); }
  return rows;
}

function validateManual(f: {
  id: string; name: string; lat: string; lon: string; url: string;
}): string | null {
  if (!ID_RE.test(f.id)) return "id must match ^[A-Za-z0-9_-]{2,32}$";
  if (!f.name.trim()) return "name is required";
  if (f.lat && (Number.isNaN(Number(f.lat)) || Number(f.lat) < -90 || Number(f.lat) > 90)) return "lat must be between -90 and 90";
  if (f.lon && (Number.isNaN(Number(f.lon)) || Number(f.lon) < -180 || Number(f.lon) > 180)) return "lon must be between -180 and 180";
  if (f.url && !URL_SCHEMES.some((s) => f.url.startsWith(s))) return `stream URL must start with one of ${URL_SCHEMES.join(", ")}`;
  return null;
}

export function OnboardDrawer({ open, onOpenChange, onDone }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);

  // Manual single-camera form.
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [department, setDepartment] = useState("");
  const [city, setCity] = useState("");
  const [district, setDistrict] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [url, setUrl] = useState("");
  const [capability, setCapability] = useState("vehicle");
  const [errors, setErrors] = useState<Record<string, string>>({});

  // CSV bulk.
  const [csvText, setCsvText] = useState("");
  const [csvRows, setCsvRows] = useState<Record<string, string>[]>([]);
  const [csvResult, setCsvResult] = useState<BulkResult | null>(null);

  function rowToPayload(row: { id: string; name: string; department: string; city: string; district: string; lat: string; lon: string; url: string; capability: string }) {
    const isHls = row.url.includes(".m3u8") || row.url.startsWith("http");
    return {
      id: row.id.trim(),
      name: row.name.trim(),
      department: row.department.trim() || undefined,
      city: row.city.trim() || undefined,
      district: row.district.trim() || undefined,
      lat: row.lat ? Number(row.lat) : undefined,
      lon: row.lon ? Number(row.lon) : undefined,
      [isHls ? "hls_url" : "rtsp_url"]: row.url.trim() || undefined,
      capability: row.capability.trim() || undefined,
    };
  }

  function validateOnBlur(field: string) {
    setErrors((e) => ({ ...e, [field]: validateManual({ id, name, lat, lon, url }) ?? "" }));
  }

  async function submitManual() {
    const err = validateManual({ id, name, lat, lon, url });
    if (err) { toast.error(err); return; }
    setBusy(true);
    try {
      const payload = rowToPayload({ id, name, department, city, district, lat, lon, url, capability });
      const cam = await api<Camera>("/api/cameras", { method: "POST", json: payload });
      toast.success(`Camera ${cam.id} registered.`);
      setId(""); setName(""); setDepartment(""); setCity(""); setDistrict(""); setLat(""); setLon(""); setUrl("");
      onDone();
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  function parseCsv(text: string) {
    const table = parseCsvTable(text);
    if (table.length === 0) { setCsvRows([]); return; }
    const header = table[0].map((h) => h.trim());
    const rows = table.slice(1).map((cells) => {
      const row: Record<string, string> = {};
      header.forEach((h, i) => { row[h] = (cells[i] ?? "").trim(); });
      return row;
    });
    setCsvRows(rows);
  }

  async function onCsvFile(file: File) {
    const text = await file.text();
    setCsvText(text);
    parseCsv(text);
  }

  async function submitCsv() {
    if (csvRows.length === 0) { toast.error("No rows to submit"); return; }
    setBusy(true);
    try {
      const cameras = csvRows.map((r) => ({
        id: r.id, name: r.name, department: r.department || undefined,
        city: r.city || undefined, district: r.district || undefined,
        lat: r.lat ? Number(r.lat) : undefined, lon: r.lon ? Number(r.lon) : undefined,
        hls_url: r.hls_url || undefined, rtsp_url: r.rtsp_url || undefined,
        capability: r.capability || undefined,
      }));
      const result = await api<BulkResult>("/api/cameras/bulk", { method: "POST", json: { cameras } });
      setCsvResult(result);
      toast.success(`${result.created} created, ${result.updated} updated, ${result.errors.length} error(s).`);
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Bulk registration failed");
    } finally {
      setBusy(false);
    }
  }

  const curl = `curl -X POST "${apiBase()}/api/cameras" \\\n  -H "X-API-Key: <your admin key>" \\\n  -H "Content-Type: application/json" \\\n  -d '{"id":"demo-cam-01","name":"Demo camera","lat":23.03,"lon":72.58,"hls_url":"https://example.com/stream.m3u8"}'`;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto p-4 sm:max-w-lg">
        <SheetHeader className="p-0">
          <SheetTitle>Onboard a camera</SheetTitle>
        </SheetHeader>

        <Tabs defaultValue="manual">
          <TabsList>
            <TabsTrigger value="manual">Manual</TabsTrigger>
            <TabsTrigger value="csv">CSV</TabsTrigger>
            <TabsTrigger value="api">API</TabsTrigger>
          </TabsList>

          <TabsContent value="manual" className="flex flex-col gap-3 pt-3">
            <Field label="Camera id" error={errors.id}>
              <Input value={id} onChange={(e) => setId(e.target.value)} onBlur={() => validateOnBlur("id")} placeholder="demo-cam-01" />
            </Field>
            <Field label="Name">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Demo camera" />
            </Field>
            <Field label="Department">
              <Input list="departments" value={department} onChange={(e) => setDepartment(e.target.value)} placeholder="Home Department" />
              <datalist id="departments">
                {KNOWN_DEPARTMENTS.map((d) => <option key={d} value={d} />)}
              </datalist>
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="City"><Input value={city} onChange={(e) => setCity(e.target.value)} /></Field>
              <Field label="District"><Input value={district} onChange={(e) => setDistrict(e.target.value)} /></Field>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Lat" error={errors.lat}>
                <Input value={lat} onChange={(e) => setLat(e.target.value)} onBlur={() => validateOnBlur("lat")} placeholder="23.03" />
              </Field>
              <Field label="Lon" error={errors.lon}>
                <Input value={lon} onChange={(e) => setLon(e.target.value)} onBlur={() => validateOnBlur("lon")} placeholder="72.58" />
              </Field>
            </div>
            <Field label="Stream URL (rtsp/hls)" error={errors.url}>
              <Input value={url} onChange={(e) => setUrl(e.target.value)} onBlur={() => validateOnBlur("url")} placeholder="https://example.com/stream.m3u8" />
            </Field>
            <Field label="Capability">
              <Input value={capability} onChange={(e) => setCapability(e.target.value)} placeholder="vehicle" />
            </Field>
            <Button onClick={submitManual} disabled={busy}>
              <UploadCloud className="size-4" /> Register camera
            </Button>
          </TabsContent>

          <TabsContent value="csv" className="flex flex-col gap-3 pt-3">
            <p className="text-xs text-muted">
              Columns: {CSV_COLUMNS.join(", ")}. Paste CSV text or choose a file, review the preview, then submit as
              one bulk call.
            </p>
            <input
              type="file"
              accept=".csv"
              disabled={busy}
              onChange={(e) => e.target.files?.[0] && onCsvFile(e.target.files[0])}
              className="text-xs"
            />
            <textarea
              className="mono h-24 w-full rounded-ctl border border-border bg-surface-2 p-2 text-xs"
              placeholder={`id,name,department,city,district,lat,lon,hls_url,rtsp_url,capability`}
              value={csvText}
              onChange={(e) => { setCsvText(e.target.value); parseCsv(e.target.value); }}
            />
            <p className="text-[11px] text-muted">Quoted fields supported; one camera per line.</p>
            {csvRows.length > 0 && (
              <div className="mono max-h-32 overflow-auto rounded-ctl border border-border bg-surface-2 p-2 text-[11px]">
                {csvRows.slice(0, 5).map((r, i) => (
                  <div key={i}>{JSON.stringify(r)}</div>
                ))}
                {csvRows.length > 5 && <div className="text-muted">…{csvRows.length - 5} more</div>}
              </div>
            )}
            <Button onClick={submitCsv} disabled={busy || csvRows.length === 0}>
              <FileUp className="size-4" /> Submit {csvRows.length || ""} row(s)
            </Button>
            {csvResult && (
              <div className="flex flex-col gap-1 text-xs">
                <span className="text-ok">{csvResult.created} created, {csvResult.updated} updated</span>
                {csvResult.errors.map((e, i) => (
                  <span key={i} className="text-bad">row {e.row} ({e.id ?? "?"}): {e.detail}</span>
                ))}
              </div>
            )}
          </TabsContent>

          <TabsContent value="api" className="flex flex-col gap-3 pt-3">
            <p className="text-xs text-muted">
              Equivalent call against the current API base. Requires an <span className="mono">X-API-Key</span> header
              for an admin key (open mode needs no key).
            </p>
            <pre className="mono overflow-x-auto whitespace-pre-wrap rounded-ctl border border-border bg-surface-2 p-2 text-xs">{curl}</pre>
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}

function Field({ label, error, children }: { label: string; error?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <Label>{label}</Label>
      {children}
      {error && <span className="text-[11px] text-bad">{error}</span>}
    </div>
  );
}
