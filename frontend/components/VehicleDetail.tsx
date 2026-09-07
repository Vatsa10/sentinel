"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, MessageSquareText, Route as RouteIcon, Sparkles } from "lucide-react";
import { api, apiUrl, ApiError } from "@/lib/api";
import { useLive } from "@/lib/live";
import type { Detection } from "@/lib/types";
import { TimeBadge } from "@/components/TimeBadge";
import { Gate } from "@/components/Gate";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

interface SimilarMatch {
  detection_id: number;
  camera_id: string;
  camera_name: string | null;
  at: string;
  vehicle_class: string | null;
  colour: string | null;
  plate_text: string | null;
  evidence: string | null;
  similarity: number;
  presented_similarity: number;
  distance_km: number;
  elapsed_s: number;
  plausible: boolean;
  plausibility: string;
  ambiguous?: boolean;
  ambiguity_note?: string;
}
interface SimilarResponse {
  query: { detection_id: number };
  matches: SimilarMatch[];
  plausible_matches: SimilarMatch[];
  method: string;
  ambiguous: boolean;
  note: string;
}
interface TrackHop {
  camera_id: string;
  camera_name?: string | null;
  at: string;
  leg_km?: number | null;
}
interface TrackResponse {
  query: string;
  hops: TrackHop[];
  hop_count: number;
  total_km: number;
  note: string;
}

export function VehicleDetail({
  detection,
  open,
  onOpenChange,
}: {
  detection: Detection | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [tab, setTab] = useState("similar");
  const qc = useQueryClient();
  const { descriptions } = useLive();

  const similarQ = useQuery({
    queryKey: ["similar", detection?.id],
    queryFn: () => api<SimilarResponse>(`/api/vehicles/${detection!.id}/similar`),
    enabled: !!detection,
    retry: false,
  });
  const trackQ = useQuery({
    queryKey: ["track", detection?.id],
    queryFn: () => api<TrackResponse>(`/api/vehicles/${detection!.id}/track`),
    enabled: !!detection && tab === "track",
    retry: false,
  });

  const describe = useMutation({
    mutationFn: () => api(`/api/detections/${detection!.id}/describe`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Description generated.");
      qc.invalidateQueries({ queryKey: ["detections"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Describe failed"),
  });

  if (!detection) return null;
  const liveDesc = descriptions[String(detection.id)] as { description?: string } | undefined;
  const attrs = (liveDesc ?? detection.attributes) as { description?: string; body_type?: string; colour?: string } | null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto p-4 sm:max-w-lg">
        <SheetHeader className="p-0">
          <SheetTitle>Detection #{detection.id}</SheetTitle>
        </SheetHeader>

        {detection.evidence && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={apiUrl(detection.evidence)} alt="" className="w-full rounded-card border border-border object-cover" />
        )}

        <table className="mono w-full text-xs">
          <tbody>
            {[
              ["Plate", detection.plate_text ?? "—"],
              ["Class", detection.vehicle_class ?? "—"],
              ["Colour", detection.colour ?? "—"],
              ["Camera", detection.camera_name ?? detection.camera_id],
              ["Confidence", detection.confidence.toFixed(2)],
            ].map(([k, v]) => (
              <tr key={k} className="border-b border-border/60 last:border-0">
                <td className="py-1.5 pr-3 text-muted">{k}</td>
                <td className="py-1.5 text-text">{v}</td>
              </tr>
            ))}
            <tr>
              <td className="py-1.5 pr-3 text-muted">Time</td>
              <td className="py-1.5"><TimeBadge det={detection} /></td>
            </tr>
          </tbody>
        </table>

        {attrs?.description && (
          <p className="rounded-ctl border border-border bg-surface-2 p-2 text-xs text-text">{attrs.description}</p>
        )}
        <Gate min="operator" reason="Describing a vehicle requires an operator key">
          <Button size="sm" variant="secondary" onClick={() => describe.mutate()} disabled={describe.isPending}>
            <MessageSquareText className="size-3.5" /> {describe.isPending ? "Describing…" : "Describe"}
          </Button>
        </Gate>

        <Tabs value={tab} onValueChange={(v) => setTab(String(v))}>
          <TabsList>
            <TabsTrigger value="similar"><Sparkles className="size-3.5" /> Similar</TabsTrigger>
            <TabsTrigger value="track"><RouteIcon className="size-3.5" /> Track</TabsTrigger>
          </TabsList>
          <TabsContent value="similar" className="flex flex-col gap-2 pt-2">
            {similarQ.isError && (
              <p className="text-xs text-muted">
                {similarQ.error instanceof ApiError ? similarQ.error.message : "No embedding for this detection."}
              </p>
            )}
            {similarQ.data?.note && <p className="text-xs text-muted">{similarQ.data.note}</p>}
            {similarQ.data?.matches.map((m) => (
              <div key={m.detection_id} className="flex items-center gap-2 rounded-ctl border border-border p-2 text-xs">
                {m.evidence && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={apiUrl(m.evidence)} alt="" className="h-10 w-14 rounded object-cover" />
                )}
                <div className="flex-1">
                  <div className="mono">{m.camera_name ?? m.camera_id} · {new Date(m.at).toLocaleTimeString("en-IN", { hour12: false })}</div>
                  <div className="text-muted">sim {m.presented_similarity.toFixed(2)} · {m.distance_km} km · {m.elapsed_s}s</div>
                </div>
                {m.ambiguous && (
                  <Badge variant="destructive" title={m.ambiguity_note}>
                    <AlertTriangle className="size-3" /> ambiguous
                  </Badge>
                )}
              </div>
            ))}
          </TabsContent>
          <TabsContent value="track" className="flex flex-col gap-2 pt-2">
            {trackQ.data?.note && <p className="text-xs text-muted">{trackQ.data.note}</p>}
            {trackQ.data?.hops.map((h, i) => (
              <div key={i} className="flex items-center justify-between rounded-ctl border border-border p-2 text-xs">
                <span className="mono">#{i + 1} {h.camera_name ?? h.camera_id}</span>
                <span className="text-muted">{new Date(h.at).toLocaleTimeString("en-IN", { hour12: false })}</span>
                <span className="mono">{h.leg_km != null ? `${h.leg_km} km` : "—"}</span>
              </div>
            ))}
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}
