import { fmtTime } from "@/lib/time";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "cn";

type Timed = Parameters<typeof fmtTime>[0];

/**
 * Renders a detection/alert timestamp using scene time when corroborated,
 * otherwise stream-relative T+ time, with a tooltip explaining the basis.
 * Never presents wall time as scene time (global constraint).
 */
export function TimeBadge({ det }: { det: Timed }) {
  const { label, basis } = fmtTime(det);
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            tabIndex={0}
            className={cn(
              "mono inline-flex items-center gap-1 text-xs",
              basis === "scene" ? "text-text" : "text-muted"
            )}
          />
        }
      >
        {label}
      </TooltipTrigger>
      <TooltipContent>
        {basis === "scene"
          ? "scene = corroborated clock"
          : "T+ = stream time (not corroborated to wall clock)"}
      </TooltipContent>
    </Tooltip>
  );
}
