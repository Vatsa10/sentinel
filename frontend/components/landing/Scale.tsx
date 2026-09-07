import Link from "next/link";
import { Server, Building2, Landmark } from "lucide-react";

const TIERS = [
  { icon: Server, title: "Edge", role: "Runs inference beside the camera; sends metadata, not video." },
  { icon: Building2, title: "Regional", role: "Aggregates a department's cameras; handles alerts and search." },
  { icon: Landmark, title: "Central", role: "State-wide view, cross-department journeys and oversight." },
];

export function Scale() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-16">
      <h2 className="text-center text-2xl font-bold text-text sm:text-3xl">
        Built to scale
      </h2>
      <div className="mt-10 grid gap-5 sm:grid-cols-3">
        {TIERS.map(({ icon: Icon, title, role }) => (
          <div key={title} className="rounded-card border border-border bg-surface p-6 text-center">
            <Icon aria-hidden="true" className="mx-auto h-6 w-6 text-accent" />
            <h3 className="mt-3 text-sm font-semibold text-text">{title}</h3>
            <p className="mt-1 text-sm text-muted">{role}</p>
          </div>
        ))}
      </div>
      <div className="mt-8 text-center">
        <Link href="/docs/high-level-design.md" className="text-sm font-medium text-accent underline underline-offset-4">
          Read the HLD
        </Link>
      </div>
    </section>
  );
}
