"use client";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ExternalLink } from "lucide-react";
import { api, apiBase, apiUrl, ApiError, setApiBase } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Camera, PipelineStatus } from "@/lib/types";
import { mergeHealth, STATE_TEXT, type CamHealthRow, type CamState } from "@/lib/health";
import { Gate } from "@/components/Gate";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

interface Who { role: "viewer" | "operator" | "admin"; name: string; enabled: boolean }
interface AuditRow { at: string; actor: string; action: string; target: string | null; detail: unknown }
interface NotifyConfig { email: boolean; webhook: boolean; smtp_host: string | null; smtp_user_masked: string; to: string | null; min_severity: string }
interface StorageReport {
  evidence: { files: number; bytes: number; mib: number; max_bytes: number | null; max_age_days: number; percent_of_budget: number | null };
  detections: { rows: number; max_rows: number | null; keep_days: number; percent_of_cap: number | null };
  alerts: { rows: number; unacknowledged: number };
  zone_events: { unacknowledged: number };
}

const PERM_MATRIX: Record<"viewer" | "operator" | "admin", string[]> = {
  viewer: ["read"],
  operator: ["read", "acknowledge", "watchlist"],
  admin: ["read", "acknowledge", "watchlist", "onboard", "pipeline", "manage"],
};
const ALL_PERMS = ["read", "acknowledge", "watchlist", "onboard", "pipeline", "manage"];

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3 rounded-card border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold text-text">{title}</h2>
      {children}
    </div>
  );
}

export default function AdminPage() {
  const { role, name, signOut } = useAuth();
  const qc = useQueryClient();
  const [base, setBase] = useState(apiBase());
  const [auditFilter, setAuditFilter] = useState("");
  const [sortKey, setSortKey] = useState<keyof CamHealthRow>("camera_id");

  const { data: who } = useQuery({ queryKey: ["whoami"], queryFn: () => api<Who>("/api/auth/whoami") });
  const { data: cameras } = useQuery({ queryKey: ["cameras"], queryFn: () => api<Camera[]>("/api/cameras") });
  const { data: status } = useQuery({ queryKey: ["pipeline-status"], queryFn: () => api<PipelineStatus>("/api/pipeline/status"), refetchInterval: 5000 });
  const { data: richHealth } = useQuery({
    queryKey: ["cameras-health"],
    queryFn: () => api<CamHealthRow[]>("/api/cameras/health").catch(() => null),
    retry: false,
  });
  const { data: audit } = useQuery({ queryKey: ["audit"], queryFn: () => api<AuditRow[]>("/api/audit?limit=200"), enabled: role === "admin" });
  const { data: storage } = useQuery({ queryKey: ["storage"], queryFn: () => api<StorageReport>("/api/storage"), enabled: role === "admin" });
  const { data: notify } = useQuery({ queryKey: ["notify-config"], queryFn: () => api<NotifyConfig>("/api/notify/config") });

  const healthRows = useMemo(() => mergeHealth(cameras ?? [], richHealth ?? null, status ?? null), [cameras, richHealth, status]);
  const sortedHealth = useMemo(
    () => [...healthRows].sort((a, b) => String(a[sortKey] ?? "").localeCompare(String(b[sortKey] ?? ""))),
    [healthRows, sortKey]
  );

  const filteredAudit = useMemo(() => {
    if (!audit) return [];
    const q = auditFilter.trim().toLowerCase();
    if (!q) return audit;
    return audit.filter((r) => `${r.actor} ${r.action} ${r.target ?? ""}`.toLowerCase().includes(q));
  }, [audit, auditFilter]);

  const prune = useMutation({
    mutationFn: (dryRun: boolean) => api(`/api/storage/prune?dry_run=${dryRun}`, { method: "POST" }),
    onSuccess: (_r, dryRun) => {
      toast.success(dryRun ? "Dry-run prune complete — see console/network for detail" : "Prune complete");
      qc.invalidateQueries({ queryKey: ["storage"] });
    },
    onError: (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Prune failed"),
  });

  const notifyTest = useMutation({
    mutationFn: () => api<{ ok?: boolean } & Record<string, unknown>>("/api/notify/test", { method: "POST" }),
    onError: (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Notify test failed"),
  });

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-text">Admin</h1>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Access">
          <div className="text-sm text-text">
            Signed in as <span className="font-semibold">{who?.name ?? name}</span> · role{" "}
            <span className="font-semibold uppercase">{who?.role ?? role}</span>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Role</TableHead>
                {ALL_PERMS.map((p) => <TableHead key={p} className="text-center">{p}</TableHead>)}
              </TableRow>
            </TableHeader>
            <TableBody>
              {(Object.keys(PERM_MATRIX) as (keyof typeof PERM_MATRIX)[]).map((r) => (
                <TableRow key={r}>
                  <TableCell className="capitalize">{r}</TableCell>
                  {ALL_PERMS.map((p) => (
                    <TableCell key={p} className="text-center">{PERM_MATRIX[r].includes(p) ? "✓" : "—"}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Button variant="outline" onClick={signOut} className="w-fit">Sign out</Button>
        </Card>

        <Card title="API base">
          <p className="mono text-xs text-muted">Current: {apiBase()}</p>
          <div className="flex gap-2">
            <Input value={base} onChange={(e) => setBase(e.target.value)} placeholder="http://localhost:8080" />
            <Button
              onClick={() => { setApiBase(base); window.location.reload(); }}
            >
              Apply
            </Button>
          </div>
        </Card>

        <Card title="Camera health">
          <div className="flex gap-2 text-xs">
            {(["camera_id", "state", "fps", "stale_s", "reconnects"] as (keyof CamHealthRow)[]).map((k) => (
              <button key={k} onClick={() => setSortKey(k)} className={`rounded-full border px-2 py-0.5 ${sortKey === k ? "border-accent text-accent" : "border-border text-muted"}`}>
                {k}
              </button>
            ))}
          </div>
          <div className="max-h-72 overflow-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Camera</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>FPS</TableHead>
                  <TableHead>Stale (s)</TableHead>
                  <TableHead>Reconnects</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedHealth.map((h) => (
                  <TableRow key={h.camera_id}>
                    <TableCell className="mono">{h.camera_id}</TableCell>
                    <TableCell className={STATE_TEXT[h.state as CamState]}>{h.state}</TableCell>
                    <TableCell className="mono">{h.fps?.toFixed(1) ?? "—"}</TableCell>
                    <TableCell className="mono">{h.stale_s?.toFixed(1) ?? "—"}</TableCell>
                    <TableCell className="mono">{h.reconnects}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Card>

        {role === "admin" && (
          <Card title="Audit log">
            <Input placeholder="Filter by actor, action or target…" value={auditFilter} onChange={(e) => setAuditFilter(e.target.value)} />
            <div className="max-h-72 overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>At</TableHead>
                    <TableHead>Actor</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Target</TableHead>
                    <TableHead>Detail</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredAudit.map((r, i) => (
                    <TableRow key={i}>
                      <TableCell className="mono text-xs">{new Date(r.at).toLocaleString("en-IN", { hour12: false })}</TableCell>
                      <TableCell className="mono text-xs">{r.actor}</TableCell>
                      <TableCell className="mono text-xs">{r.action}</TableCell>
                      <TableCell className="mono text-xs">{r.target ?? "—"}</TableCell>
                      <TableCell className="mono max-w-xs truncate text-xs" title={JSON.stringify(r.detail)}>{JSON.stringify(r.detail)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </Card>
        )}

        {role === "admin" && storage && (
          <Card title="Storage & retention">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-muted">Evidence</div>
                <div className="mono">{storage.evidence.mib} MiB · {storage.evidence.files} files{storage.evidence.percent_of_budget != null ? ` (${storage.evidence.percent_of_budget}% of budget)` : ""}</div>
              </div>
              <div>
                <div className="text-muted">Detections</div>
                <div className="mono">{storage.detections.rows} rows{storage.detections.percent_of_cap != null ? ` (${storage.detections.percent_of_cap}% of cap)` : ""}</div>
              </div>
              <div>
                <div className="text-muted">Alerts</div>
                <div className="mono">{storage.alerts.rows} rows · {storage.alerts.unacknowledged} unacknowledged</div>
              </div>
              <div>
                <div className="text-muted">Zone events</div>
                <div className="mono">{storage.zone_events.unacknowledged} unacknowledged</div>
              </div>
            </div>
            <div className="flex gap-2">
              <Gate min="admin" reason="Pruning storage requires an admin key">
                <Button variant="outline" onClick={() => prune.mutate(true)} disabled={prune.isPending}>Dry-run prune</Button>
              </Gate>
              <Gate min="admin" reason="Pruning storage requires an admin key">
                <Button
                  variant="destructive"
                  onClick={() => { if (confirm("This permanently deletes evidence past budget. Continue?")) prune.mutate(false); }}
                  disabled={prune.isPending}
                >
                  Prune
                </Button>
              </Gate>
            </div>
          </Card>
        )}

        <Card title="Notifications">
          {notify && (
            <div className="text-sm text-text">
              <div>Email: {notify.email ? "configured" : "not configured"}{notify.to ? ` → ${notify.to}` : ""}</div>
              <div>Webhook: {notify.webhook ? "configured" : "not configured"}</div>
              <div className="mono text-xs text-muted">SMTP user: {notify.smtp_user_masked} · min severity: {notify.min_severity}</div>
            </div>
          )}
          <Gate min="operator" reason="Sending a test notification requires an operator key">
            <Button onClick={() => notifyTest.mutate()} disabled={notifyTest.isPending}>Send test</Button>
          </Gate>
          {notifyTest.data ? <pre className="mono max-h-32 overflow-auto rounded-ctl border border-border bg-surface-2 p-2 text-xs">{JSON.stringify(notifyTest.data, null, 2)}</pre> : null}
        </Card>

        <Card title="Links">
          <div className="flex flex-col gap-2 text-sm">
            <a href={apiUrl("/legacy/")} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-accent hover:underline">
              <ExternalLink className="size-3.5" /> Legacy console
            </a>
            <a href={apiUrl("/docs")} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-accent hover:underline">
              <ExternalLink className="size-3.5" /> OpenAPI docs
            </a>
            <a href="https://github.com" target="_blank" rel="noreferrer" className="flex items-center gap-1 text-accent hover:underline">
              <ExternalLink className="size-3.5" /> Repository
            </a>
          </div>
        </Card>
      </div>
    </div>
  );
}
