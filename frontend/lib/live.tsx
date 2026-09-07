"use client";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { PipelineStatus } from "./types";
import { connect } from "./ws";

/**
 * One message received on /ws/alerts that is not a `{"type":"ping"}`
 * keepalive or an attributes update. Keys per netra/pipeline.py:523-566
 * `_raise_alert`.
 */
export interface AlertEvent {
  kind?: string;
  alert_id?: number;
  detection_id?: number;
  plate?: string;
  camera_id?: string;
  score?: number;
  severity?: string;
  evidence?: string;
  [k: string]: unknown;
}

interface AttributesMsg {
  kind: "attributes";
  detection_id?: number;
  [k: string]: unknown;
}

interface LiveCtx {
  alerts: AlertEvent[];
  status: PipelineStatus | null;
  connected: boolean;
  descriptions: Record<string, unknown>;
}

const LiveContext = createContext<LiveCtx | null>(null);

const MAX_MESSAGES = 200;

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const [alerts, setAlerts] = useState<AlertEvent[]>([]);
  const [descriptions, setDescriptions] = useState<Record<string, unknown>>({});
  const [connected, setConnected] = useState(false);
  const descriptionsRef = useRef<Record<string, unknown>>({});

  const { data: status } = useQuery({
    queryKey: ["pipeline"],
    queryFn: () => api<PipelineStatus>("/api/pipeline/status"),
    refetchInterval: 4000,
  });

  useEffect(() => {
    const dispose = connect(
      "/ws/alerts",
      (m: Record<string, unknown>) => {
        if (m && (m as AttributesMsg).kind === "attributes") {
          const key = String((m as AttributesMsg).detection_id ?? "");
          descriptionsRef.current = { ...descriptionsRef.current, [key]: m };
          setDescriptions(descriptionsRef.current);
          return;
        }
        setAlerts((prev) => {
          const next = [...prev, m as AlertEvent];
          return next.length > MAX_MESSAGES ? next.slice(next.length - MAX_MESSAGES) : next;
        });
      },
      setConnected
    );
    return dispose;
  }, []);

  return (
    <LiveContext.Provider value={{ alerts, status: status ?? null, connected, descriptions }}>
      {children}
    </LiveContext.Provider>
  );
}

export function useLive() {
  const c = useContext(LiveContext);
  if (!c) throw new Error("LiveProvider missing");
  return c;
}
