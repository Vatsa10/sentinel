"use client";
import { useMemo } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from "recharts";

export interface CountsPoint {
  bucket: string;
  count: number;
  expectedLow?: number | null;
  expectedHigh?: number | null;
}

function reducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; }
}

/**
 * Traffic count history for one camera, with an optional baseline band
 * (mean ± z*stdev) overlaid as a shaded expected range.
 */
export function CountsChart({
  data,
  sceneTime = false,
  label = "vehicle count",
}: {
  data: CountsPoint[];
  sceneTime?: boolean;
  label?: string;
}) {
  const animate = !reducedMotion();
  const summary = useMemo(() => {
    if (data.length === 0) return "No data.";
    const total = data.reduce((s, d) => s + d.count, 0);
    const max = Math.max(...data.map((d) => d.count));
    return `${data.length} buckets, ${total} total ${label}, peak ${max}.`;
  }, [data, label]);

  // Stacked-area trick for a true [expectedLow, expectedHigh] corridor: an
  // invisible base area up to expectedLow, then a visible band whose height
  // is (expectedHigh - expectedLow) stacked on top of it.
  const chartData = useMemo(
    () =>
      data.map((d) => ({
        ...d,
        bandHeight:
          d.expectedLow != null && d.expectedHigh != null
            ? Math.max(0, d.expectedHigh - d.expectedLow)
            : null,
      })),
    [data]
  );
  const hasBand = data.some((d) => d.expectedLow != null && d.expectedHigh != null);

  return (
    <div
      role="img"
      aria-label={`History chart of ${label} over ${sceneTime ? "scene time" : "stream time buckets"}. ${summary}`}
      className="h-64 w-full"
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="countsFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#5b8def" stopOpacity={0.25} />
              <stop offset="100%" stopColor="#5b8def" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#22304f" strokeDasharray="3 3" />
          <XAxis
            dataKey="bucket"
            tick={{ fontSize: 10, fill: "#8a97ad" }}
            label={{ value: sceneTime ? "scene time" : "stream time buckets", position: "insideBottom", offset: -2, fontSize: 10, fill: "#8a97ad" }}
          />
          <YAxis tick={{ fontSize: 10, fill: "#8a97ad" }} allowDecimals={false} />
          <Tooltip
            contentStyle={{ background: "#101826", border: "1px solid #22304f", fontSize: 12 }}
            labelStyle={{ color: "#e5e9f0" }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {hasBand && (
            <>
              <Area
                type="monotone"
                dataKey="expectedLow"
                stackId="band"
                name="expected low"
                legendType="none"
                stroke="none"
                fill="transparent"
                isAnimationActive={animate}
              />
              <Area
                type="monotone"
                dataKey="bandHeight"
                stackId="band"
                name="Baseline band (mean ± z)"
                stroke="none"
                fill="#8a97ad"
                fillOpacity={0.2}
                isAnimationActive={animate}
              />
            </>
          )}
          <Area
            type="monotone"
            dataKey="count"
            name={label}
            stroke="#5b8def"
            fill="url(#countsFill)"
            strokeWidth={2}
            isAnimationActive={animate}
          />
          <ReferenceLine y={0} stroke="#22304f" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
