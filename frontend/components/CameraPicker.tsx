"use client";
import { useMemo, useState } from "react";
import { Plus, Search, Sparkles } from "lucide-react";
import { cn } from "cn";
import type { Camera } from "@/lib/types";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** Ahmedabad half of the time-aligned preset (brief, task B4 step 2). */
export const AHMEDABAD_PRESET = ["cam01", "cam02", "cam03", "cam04", "cam05", "cam13", "cam14", "cam15"];
/** Junagadh half of the time-aligned preset (brief, task B4 step 2). */
export const JUNAGADH_PRESET = ["cam08", "cam09", "cam10", "cam11"];
/** Both groups together — kept for callers (e.g. the wall's default) that want the full set. */
export const TIME_ALIGNED_PRESET = [...AHMEDABAD_PRESET, ...JUNAGADH_PRESET];

export function CameraPicker({
  cameras,
  current,
  onAdd,
  onAddMany,
}: {
  cameras: Camera[];
  current: string[];
  onAdd: (id: string) => void;
  onAddMany: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [group, setGroup] = useState<string | null>(null);
  const [capability, setCapability] = useState<string | null>(null);

  const groups = useMemo(() => {
    const s = new Set<string>();
    cameras.forEach((c) => {
      if (c.city) s.add(c.city);
      if (c.time_group) s.add(c.time_group);
    });
    return Array.from(s).sort();
  }, [cameras]);

  const capabilities = useMemo(() => {
    const s = new Set<string>();
    cameras.forEach((c) => c.capability && s.add(c.capability));
    return Array.from(s).sort();
  }, [cameras]);

  const filtered = cameras.filter((c) => {
    if (q && !`${c.id} ${c.name}`.toLowerCase().includes(q.toLowerCase())) return false;
    if (group && c.city !== group && c.time_group !== group) return false;
    if (capability && c.capability !== capability) return false;
    return true;
  });

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <Button size="sm" className="h-11 sm:h-8">
            <Plus className="size-4" />
            Add cameras
          </Button>
        }
      />
      <SheetContent side="right" className="w-full max-w-sm p-0">
        <SheetTitle className="p-3">Add cameras</SheetTitle>
        <div className="flex flex-col gap-3 border-b border-border p-3">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search id or name…"
              className="pl-8"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Button
              size="sm"
              variant="secondary"
              className="h-11 sm:h-8"
              onClick={() => onAddMany(AHMEDABAD_PRESET)}
            >
              <Sparkles className="size-4" />
              Ahmedabad group ({AHMEDABAD_PRESET.length})
            </Button>
            <Button
              size="sm"
              variant="secondary"
              className="h-11 sm:h-8"
              onClick={() => onAddMany(JUNAGADH_PRESET)}
            >
              <Sparkles className="size-4" />
              Junagadh group ({JUNAGADH_PRESET.length})
            </Button>
            <Button
              size="sm"
              variant="secondary"
              className="h-11 sm:h-8"
              onClick={() => onAddMany(TIME_ALIGNED_PRESET)}
            >
              <Sparkles className="size-4" />
              Both groups ({TIME_ALIGNED_PRESET.length})
            </Button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {groups.map((g) => (
              <button
                key={g}
                onClick={() => setGroup(group === g ? null : g)}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-xs",
                  group === g
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-border text-muted hover:text-text"
                )}
              >
                {g}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {capabilities.map((cap) => (
              <button
                key={cap}
                onClick={() => setCapability(capability === cap ? null : cap)}
                className={cn(
                  "mono rounded-full border px-2.5 py-1 text-xs",
                  capability === cap
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-border text-muted hover:text-text"
                )}
              >
                {cap}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {filtered.map((c) => {
            const added = current.includes(c.id);
            return (
              <button
                key={c.id}
                disabled={added}
                onClick={() => onAdd(c.id)}
                className="flex w-full items-center gap-2 rounded-ctl px-2 py-2.5 text-left text-sm hover:bg-surface-2 disabled:opacity-40"
              >
                <span className="mono text-xs text-muted">{c.id}</span>
                <span className="truncate">{c.name}</span>
                <span className="ml-auto text-xs text-muted">{added ? "Added" : "+"}</span>
              </button>
            );
          })}
          {filtered.length === 0 && (
            <div className="p-4 text-center text-sm text-muted">No cameras match.</div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
