import { Eye, Wrench, Shield } from "lucide-react";
import { cn } from "cn";
import { useAuth, type Role } from "@/lib/auth";

const CONFIG: Record<Role, { icon: typeof Eye; label: string; cls: string }> = {
  viewer: { icon: Eye, label: "Viewer", cls: "text-muted bg-surface-2 border-border" },
  operator: { icon: Wrench, label: "Operator", cls: "text-info bg-surface-2 border-info/40" },
  admin: { icon: Shield, label: "Admin", cls: "text-accent bg-surface-2 border-accent/40" },
};

export function RoleBadge() {
  const { role } = useAuth();
  const { icon: Icon, label, cls } = CONFIG[role];
  return (
    <span
      className={cn(
        "mono inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium",
        cls
      )}
    >
      <Icon className="size-3.5" />
      {label}
    </span>
  );
}
