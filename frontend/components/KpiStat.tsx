import { cn } from "cn";

/** One KPI tile in the Overview row: big mono number, small muted label, optional trend text. */
export function KpiStat({
  label,
  value,
  trend,
  className,
}: {
  label: string;
  value: string;
  trend?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-card border border-border bg-surface px-4 py-3",
        className
      )}
    >
      <div className="mono text-[32px] leading-none font-semibold text-text">{value}</div>
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      {trend && <div className="mono text-xs text-muted">{trend}</div>}
    </div>
  );
}
