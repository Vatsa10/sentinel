import { KeyRound, ShieldCheck, HardDriveDownload, Lock } from "lucide-react";

const POINTS = [
  { icon: ShieldCheck, title: "RBAC roles", line: "Viewer, operator and admin roles gate every action." },
  { icon: HardDriveDownload, title: "Audit trail", line: "Every search, alert and sign-in is logged." },
  { icon: Lock, title: "No central video storage", line: "Video stays at the edge; only metadata is centralised." },
  { icon: KeyRound, title: "Keys never in the bundle", line: "API keys live in local storage, never shipped in frontend code." },
];

export function Security() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-16">
      <h2 className="text-center text-2xl font-bold text-text sm:text-3xl">
        Security by design
      </h2>
      <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {POINTS.map(({ icon: Icon, title, line }) => (
          <div key={title} className="rounded-card border border-border bg-surface p-5">
            <Icon aria-hidden="true" className="h-6 w-6 text-accent" />
            <h3 className="mt-3 text-sm font-semibold text-text">{title}</h3>
            <p className="mt-1 text-sm text-muted">{line}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
