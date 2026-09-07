"use client";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { toast } from "sonner";
import { Plus, Trash2, Upload, Sparkles } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { WatchlistEntry } from "@/lib/types";
import { Gate } from "@/components/Gate";
import { EmptyState } from "@/components/EmptyState";
import { WatchlistForm, type WatchlistFormValues } from "@/components/WatchlistForm";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger, DialogClose,
} from "@/components/ui/dialog";

const SEV_CLS: Record<string, string> = {
  critical: "text-bad bg-bad/10 border-bad/40",
  high: "text-warn bg-warn/10 border-warn/40",
  medium: "text-info bg-info/10 border-info/40",
  low: "text-muted bg-surface-2 border-border",
};

function parseCsv(text: string): Partial<WatchlistFormValues>[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length === 0) return [];
  const header = lines[0].split(",").map((h) => h.trim().toLowerCase());
  return lines.slice(1).map((line) => {
    const cells = line.split(",").map((c) => c.trim());
    const row: Record<string, string> = {};
    header.forEach((h, i) => { row[h] = cells[i] ?? ""; });
    return row as unknown as Partial<WatchlistFormValues>;
  });
}

export default function WatchlistPage() {
  const qc = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<WatchlistEntry | null>(null);
  const [importProgress, setImportProgress] = useState<{ done: number; total: number } | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const { data, isLoading } = useQuery({ queryKey: ["watchlist"], queryFn: () => api<WatchlistEntry[]>("/api/watchlist") });

  const add = useMutation({
    mutationFn: (v: WatchlistFormValues) => api("/api/watchlist", { method: "POST", json: v }),
    onSuccess: () => { toast.success("Entry added"); setAddOpen(false); qc.invalidateQueries({ queryKey: ["watchlist"] }); },
    onError: (e: unknown) => { if (e instanceof ApiError) toast.error(e.message); },
  });

  const del = useMutation({
    mutationFn: (id: number) => api(`/api/watchlist/${id}`, { method: "DELETE" }),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ["watchlist"] });
      const entry = deleteTarget;
      setDeleteTarget(null);
      if (!entry) return;
      let undone = false;
      toast(`Deleted ${entry.plate}`, {
        action: {
          label: "Undo",
          onClick: async () => {
            undone = true;
            try {
              await api("/api/watchlist", {
                method: "POST",
                json: {
                  plate: entry.plate, category: entry.category, severity: entry.severity,
                  owner_name: entry.owner_name, vehicle_make: entry.vehicle_make,
                  vehicle_colour: entry.vehicle_colour, vehicle_class: entry.vehicle_class,
                  case_ref: entry.case_ref, source_db: entry.source_db, notes: entry.notes,
                },
              });
              qc.invalidateQueries({ queryKey: ["watchlist"] });
              toast.success("Restored");
            } catch (e) { if (e instanceof ApiError) toast.error(e.message); }
          },
        },
        duration: 5000,
      });
      void id; void undone;
    },
    onError: (e: unknown) => { if (e instanceof ApiError) toast.error(e.message); },
  });

  const seed = useMutation({
    mutationFn: () => api("/api/watchlist/seed", { method: "POST" }),
    onSuccess: () => { toast.success("Demonstration set seeded"); qc.invalidateQueries({ queryKey: ["watchlist"] }); },
    onError: (e: unknown) => { if (e instanceof ApiError) toast.error(e.message); },
  });

  const importCsv = async (file: File) => {
    const text = await file.text();
    const rows = parseCsv(text);
    setImportProgress({ done: 0, total: rows.length });
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (!r.plate) { setImportProgress({ done: i + 1, total: rows.length }); continue; }
      try {
        await api("/api/watchlist", {
          method: "POST",
          json: {
            plate: String(r.plate).toUpperCase(),
            category: r.category || "suspect",
            severity: r.severity || "medium",
            owner_name: r.owner_name, vehicle_make: r.vehicle_make,
            vehicle_colour: r.vehicle_colour, vehicle_class: r.vehicle_class,
            case_ref: r.case_ref, source_db: r.source_db || "CSV_IMPORT", notes: r.notes,
          },
        });
      } catch (e) { if (e instanceof ApiError) toast.error(`${r.plate}: ${e.message}`); }
      setImportProgress({ done: i + 1, total: rows.length });
    }
    qc.invalidateQueries({ queryKey: ["watchlist"] });
    toast.success(`Imported ${rows.length} rows`);
    setImportProgress(null);
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Watchlist</h1>
        <div className="flex flex-wrap gap-2">
          <Gate min="operator" reason="Sign in as operator or admin to seed demonstration data">
            <Button size="sm" variant="secondary" onClick={() => seed.mutate()} disabled={seed.isPending}>
              <Sparkles className="size-4" /> Seed demonstration set
            </Button>
          </Gate>
          <Gate min="operator" reason="Sign in as operator or admin to import a CSV">
            <Button size="sm" variant="secondary" onClick={() => fileRef.current?.click()}>
              <Upload className="size-4" /> Import CSV
            </Button>
          </Gate>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importCsv(f); e.target.value = ""; }}
          />
          <Dialog open={addOpen} onOpenChange={setAddOpen}>
            <Gate min="operator" reason="Sign in as operator or admin to add watchlist entries">
              <DialogTrigger render={<Button size="sm"><Plus className="size-4" /> Add entry</Button>} />
            </Gate>
            <DialogContent className="max-w-lg">
              <DialogHeader><DialogTitle>Add watchlist entry</DialogTitle></DialogHeader>
              <WatchlistForm onSubmit={(v) => add.mutate(v)} submitting={add.isPending} />
              <DialogFooter>
                <DialogClose render={<Button variant="ghost">Cancel</Button>} />
                <Button type="submit" form="watchlist-form" disabled={add.isPending}>Save</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {importProgress && (
        <div className="text-sm text-muted">Importing… {importProgress.done}/{importProgress.total}</div>
      )}

      {!isLoading && (data ?? []).length === 0 ? (
        <EmptyState icon={Plus} title="Watchlist is empty" body="Add an entry or seed the demonstration set." />
      ) : (
        <div className="overflow-x-auto rounded-card border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Plate</TableHead>
                <TableHead>Category</TableHead>
                <TableHead>Severity</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>FIR ref</TableHead>
                <TableHead>Hits</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data ?? []).map((e) => (
                <TableRow key={e.id}>
                  <TableCell className="mono">{e.plate}</TableCell>
                  <TableCell><Badge variant="outline">{e.category}</Badge></TableCell>
                  <TableCell>
                    <span className={`mono inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${SEV_CLS[e.severity] ?? SEV_CLS.medium}`}>
                      {e.severity}
                    </span>
                  </TableCell>
                  <TableCell className="max-w-[16rem] truncate text-muted">
                    {[e.owner_name, e.vehicle_make, e.vehicle_colour, e.vehicle_class].filter(Boolean).join(" · ") || "—"}
                  </TableCell>
                  <TableCell className="text-muted">{e.source_db ?? "—"}</TableCell>
                  <TableCell className="text-muted">{e.case_ref ?? "—"}</TableCell>
                  <TableCell>
                    <Link className="text-accent underline" href={`/console/alerts?plate=${encodeURIComponent(e.plate)}`}>
                      View alerts
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Gate min="operator" reason="Sign in as operator or admin to delete watchlist entries">
                      <Button size="sm" variant="ghost" onClick={() => setDeleteTarget(e)}>
                        <Trash2 className="size-4" />
                      </Button>
                    </Gate>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={!!deleteTarget} onOpenChange={(o) => { if (!o) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Delete {deleteTarget?.plate}?</DialogTitle></DialogHeader>
          <p className="text-sm text-muted">This removes the entry from the watchlist. You can undo within 5 seconds after deleting.</p>
          <DialogFooter>
            <DialogClose render={<Button variant="ghost">Cancel</Button>} />
            <Button variant="destructive" onClick={() => deleteTarget && del.mutate(deleteTarget.id)}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
