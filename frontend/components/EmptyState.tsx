import type { LucideIcon } from "lucide-react";

export function EmptyState({
  icon: Icon,
  title,
  body,
  action,
}: {
  icon: LucideIcon;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-border bg-surface px-6 py-12 text-center">
      <Icon className="size-8 text-muted" />
      <div className="text-base font-semibold text-text">{title}</div>
      <div className="max-w-sm text-sm text-muted">{body}</div>
      {action}
    </div>
  );
}
