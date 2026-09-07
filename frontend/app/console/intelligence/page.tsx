"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Network, Copy } from "lucide-react";
import { api } from "@/lib/api";
import type { AnomaliesResponse, ClonedPlatesResponse, JourneysResponse } from "@/lib/types";
import { JourneyCard } from "@/components/JourneyCard";
import { ClonePairCard } from "@/components/ClonePairCard";
import { AnomalyTable } from "@/components/AnomalyTable";
import { EmptyState } from "@/components/EmptyState";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";

// netra/core/geo.py TIME_GROUPS
const TIME_GROUPS = ["ahmedabad-13jun", "junagadh-13jun"];

export default function IntelligencePage() {
  const [group, setGroup] = useState(TIME_GROUPS[0]);

  const { data: journeys, isLoading: journeysLoading } = useQuery({
    queryKey: ["journeys", group],
    queryFn: () => api<JourneysResponse>(`/api/analytics/journeys?group=${encodeURIComponent(group)}`),
  });

  const { data: clones, isLoading: clonesLoading } = useQuery({
    queryKey: ["cloned-plates"],
    queryFn: () => api<ClonedPlatesResponse>("/api/analytics/cloned-plates"),
  });

  const { data: anomalies } = useQuery({
    queryKey: ["anomalies"],
    queryFn: () => api<AnomaliesResponse>("/api/analytics/anomalies?include_normal=true"),
    refetchInterval: 8000,
  });

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-text">Intelligence</h1>

      <Tabs defaultValue="journeys">
        <TabsList>
          <TabsTrigger value="journeys">Journeys</TabsTrigger>
          <TabsTrigger value="clones">Cloned plates</TabsTrigger>
          <TabsTrigger value="anomalies">Anomalies</TabsTrigger>
        </TabsList>

        <TabsContent value="journeys" className="flex flex-col gap-3 pt-3">
          <div className="flex items-center gap-2">
            <label className="text-xs text-muted">Time group</label>
            <select
              className="h-9 rounded-ctl border border-border bg-surface-2 px-2 text-sm text-text"
              value={group}
              onChange={(e) => setGroup(e.target.value)}
            >
              {TIME_GROUPS.map((g) => (
                <option key={g} value={g}>{g}</option>
              ))}
            </select>
          </div>

          {journeysLoading ? (
            <Skeleton className="h-48 w-full rounded-card" />
          ) : !journeys || journeys.journeys.length === 0 ? (
            <EmptyState
              icon={Network}
              title="No journeys to show"
              body="Cross-camera journeys require corroborated clocks on both cameras; in the sandbox only cam13 corroborates, so this list is empty unless your own feeds provide overlays."
            />
          ) : (
            <div className="flex flex-col gap-3">
              {journeys.journeys.map((j, i) => (
                <JourneyCard key={i} journey={j} />
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="clones" className="flex flex-col gap-3 pt-3">
          {clonesLoading ? (
            <Skeleton className="h-48 w-full rounded-card" />
          ) : !clones || clones.findings.length === 0 ? (
            <EmptyState icon={Copy} title="No cloned plates found" body={clones?.note ?? "No pairs pass the plausibility check."} />
          ) : (
            <>
              {clones.truncated && (
                <p className="text-[11px] text-muted">Results capped to the last 72 h / N rows.</p>
              )}
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {clones.findings.map((f, i) => (
                  <ClonePairCard key={i} finding={f} />
                ))}
              </div>
            </>
          )}
        </TabsContent>

        <TabsContent value="anomalies" className="flex flex-col gap-3 pt-3">
          <AnomalyTable data={anomalies} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
