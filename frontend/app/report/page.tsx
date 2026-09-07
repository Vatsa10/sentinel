"use client";
import { useMemo, useState } from "react";
import { ExternalLink } from "lucide-react";
import { apiUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function ReportPage() {
  const [hours, setHours] = useState("24");
  const [plate, setPlate] = useState("");
  const [cameras, setCameras] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const url = useMemo(() => {
    const p = new URLSearchParams();
    p.set("hours", hours || "24");
    if (plate.trim()) p.set("plate", plate.trim());
    if (cameras.trim()) p.set("cameras", cameras.trim());
    return apiUrl(`/api/report?${p.toString()}`);
  }, [hours, plate, cameras]);

  function generate() {
    window.open(url, "_blank", "noopener,noreferrer");
    setPreviewUrl(url);
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4 p-6">
      <h1 className="text-lg font-semibold text-text">Operational report</h1>
      <p className="text-sm text-muted">
        Detected vehicles and plates, watchlist matches, zone events and per-camera activity for the
        selected window. Opens as a printable page — use the browser&apos;s Save as PDF.
      </p>

      <div className="grid grid-cols-1 gap-3 rounded-card border border-border bg-surface p-4 sm:grid-cols-3">
        <div className="flex flex-col gap-1">
          <Label>Hours</Label>
          <Input type="number" min={1} max={720} value={hours} onChange={(e) => setHours(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <Label>Plate (optional)</Label>
          <Input className="mono uppercase" value={plate} onChange={(e) => setPlate(e.target.value)} placeholder="GJ01AB1234" />
        </div>
        <div className="flex flex-col gap-1">
          <Label>Cameras (optional, comma-separated)</Label>
          <Input value={cameras} onChange={(e) => setCameras(e.target.value)} placeholder="cam13,cam14" />
        </div>
        <div className="sm:col-span-3">
          <Button onClick={generate}>
            <ExternalLink className="size-4" /> Generate report
          </Button>
        </div>
      </div>

      {previewUrl && (
        <div className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-text">Preview</h2>
          <iframe
            src={previewUrl}
            title="Operational report preview"
            className="h-[70vh] w-full rounded-card border border-border bg-white"
          />
        </div>
      )}
    </div>
  );
}
