import { cloneElement } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuth, type Role } from "@/lib/auth";

/**
 * Wraps a single element (typically a button) and disables it with an
 * explanatory tooltip when the signed-in role is below `min`.
 */
export function Gate({
  min,
  reason,
  children,
}: {
  min: Role;
  reason: string;
  children: React.ReactElement<{ disabled?: boolean }>;
}) {
  const { can } = useAuth();
  if (can(min)) return children;
  return (
    <Tooltip>
      <TooltipTrigger
        render={<span tabIndex={0} className="inline-flex cursor-not-allowed" />}
      >
        {cloneElement(children, { disabled: true })}
      </TooltipTrigger>
      <TooltipContent>{reason}</TooltipContent>
    </Tooltip>
  );
}
