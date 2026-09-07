"use client";
import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Grid2x2,
  Map,
  Car,
  Bell,
  ListChecks,
  Shapes,
  Activity,
  Network,
  MessageSquare,
  ShieldCheck,
  Menu,
  Circle,
  type LucideIcon,
} from "lucide-react";
import { cn } from "cn";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Brand } from "@/components/Brand";
import { RoleBadge } from "@/components/RoleBadge";
import { SignInDialog } from "@/components/SignInDialog";
import { useAuth } from "@/lib/auth";
import { useLive } from "@/lib/live";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

const NAV: NavItem[] = [
  { href: "/console", label: "Overview", icon: LayoutDashboard },
  { href: "/console/wall", label: "Video Wall", icon: Grid2x2 },
  { href: "/console/map", label: "GIS Map", icon: Map },
  { href: "/console/vehicles", label: "Vehicles", icon: Car },
  { href: "/console/alerts", label: "Alerts", icon: Bell },
  { href: "/console/watchlist", label: "Watchlist", icon: ListChecks },
  { href: "/console/zones", label: "Zones & Intrusion", icon: Shapes },
  { href: "/console/traffic", label: "Traffic", icon: Activity },
  { href: "/console/intelligence", label: "Intelligence", icon: Network },
  { href: "/console/assistant", label: "Assistant", icon: MessageSquare },
  { href: "/console/admin", label: "Admin", icon: ShieldCheck },
];

function NavLink({
  item,
  active,
  collapsed,
  onNavigate,
}: {
  item: NavItem;
  active: boolean;
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      className={cn(
        "flex items-center gap-3 rounded-ctl border-l-2 border-transparent px-3 py-2 text-sm text-muted transition-colors hover:bg-surface-2 hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
        active && "border-accent bg-surface-2 text-text",
        collapsed && "justify-center px-0"
      )}
      title={collapsed ? item.label : undefined}
    >
      <Icon className="size-4 shrink-0" />
      {!collapsed && <span>{item.label}</span>}
    </Link>
  );
}

function NavList({
  pathname,
  collapsed,
  onNavigate,
}: {
  pathname: string;
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  return (
    <nav className="flex flex-col gap-1 p-2">
      {NAV.map((item) => (
        <NavLink
          key={item.href}
          item={item}
          active={
            item.href === "/console"
              ? pathname === "/console"
              : pathname.startsWith(item.href)
          }
          collapsed={collapsed}
          onNavigate={onNavigate}
        />
      ))}
    </nav>
  );
}

function PipelinePill() {
  const { status } = useLive();
  const running = status?.running ?? false;
  const cams = status?.cameras?.length ?? 0;
  const ms = status?.inference?.infer_ms;
  return (
    <span
      className={cn(
        "mono inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
        running
          ? "border-ok/40 bg-surface-2 text-ok"
          : "border-border bg-surface-2 text-muted"
      )}
    >
      <Circle className={cn("size-2 fill-current", running ? "text-ok" : "text-muted")} />
      {running
        ? `Running · ${cams} cams · ${ms != null ? ms.toFixed(0) : "–"} ms`
        : "Stopped"}
    </span>
  );
}

function BackendDot() {
  const { connected } = useLive();
  return (
    <Tooltip>
      <TooltipTrigger
        render={<span tabIndex={0} className="inline-flex items-center" />}
      >
        <Circle
          className={cn(
            "size-2.5 fill-current",
            connected ? "text-ok" : "text-bad"
          )}
        />
      </TooltipTrigger>
      <TooltipContent>
        {connected ? "Live socket connected" : "Live socket disconnected"}
      </TooltipContent>
    </Tooltip>
  );
}

function TimeBasisChip() {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            tabIndex={0}
            className="mono hidden items-center rounded-full border border-border bg-surface-2 px-2.5 py-1 text-xs text-muted sm:inline-flex"
          />
        }
      >
        scene · T+
      </TooltipTrigger>
      <TooltipContent>
        scene = corroborated clock · T+ = stream time
      </TooltipContent>
    </Tooltip>
  );
}

function TopBar({ onMenu }: { onMenu: () => void }) {
  const { role, signOut } = useAuth();
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-surface px-3">
      <Button
        variant="ghost"
        size="icon-sm"
        className="md:hidden"
        onClick={onMenu}
        aria-label="Open navigation"
      >
        <Menu className="size-4" />
      </Button>
      <Brand size="sm" />
      <div className="ml-auto flex items-center gap-2">
        <PipelinePill />
        <BackendDot />
        <TimeBasisChip />
        <RoleBadge />
        {role === "viewer" ? (
          <SignInDialog>
            <Button size="sm" variant="outline">
              Sign in
            </Button>
          </SignInDialog>
        ) : (
          <>
            {/* No trigger here: mounted so a 401/403 elsewhere can still
                reopen it (AuthProvider controls `open`), even though the
                signed-in top bar shows Sign out instead of a Sign in button. */}
            <SignInDialog />
            <Button size="sm" variant="outline" onClick={signOut}>
              Sign out
            </Button>
          </>
        )}
      </div>
    </header>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <TopBar onMenu={() => setMobileOpen(true)} />
      <div className="flex min-h-0 flex-1">
        <aside
          className={cn(
            "hidden shrink-0 border-r border-border bg-surface md:block",
            collapsed ? "w-16" : "w-56 lg:w-56",
            "lg:w-56"
          )}
        >
          <div className="flex justify-end p-1 lg:hidden">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => setCollapsed((c) => !c)}
              aria-label="Toggle sidebar"
            >
              <Menu className="size-4" />
            </Button>
          </div>
          <NavList pathname={pathname} collapsed={collapsed} />
        </aside>
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetContent side="left" className="p-0">
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <div className="p-3">
              <Brand size="sm" />
            </div>
            <NavList pathname={pathname} onNavigate={() => setMobileOpen(false)} />
          </SheetContent>
        </Sheet>
        <main className="min-w-0 flex-1 overflow-y-auto p-4">{children}</main>
      </div>
    </div>
  );
}
