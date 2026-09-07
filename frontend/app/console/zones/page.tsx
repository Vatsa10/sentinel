"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Camera } from "@/lib/types";
import { ZoneEditor } from "@/components/ZoneEditor";
import { ZoneEventList } from "@/components/ZoneEventList";

export default function ZonesPage() {
  const { data: cameras } = useQuery({ queryKey: ["cameras"], queryFn: () => api<Camera[]>("/api/cameras") });

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold">Zones &amp; Intrusion</h1>
      {cameras && cameras.length > 0 ? (
        <ZoneEditor cameras={cameras} />
      ) : (
        <div className="text-sm text-muted">Loading cameras…</div>
      )}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted">Recent events</h2>
        <ZoneEventList />
      </div>
    </div>
  );
}
