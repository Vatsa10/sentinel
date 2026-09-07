import {
  LayoutGrid,
  MapPinned,
  ScanLine,
  BellRing,
  Route,
  Copy,
  ShieldAlert,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

const CAPABILITIES: { icon: LucideIcon; title: string; line: string }[] = [
  { icon: LayoutGrid, title: "Unified viewing", line: "Every department's cameras in one console, one login." },
  { icon: MapPinned, title: "Registry & GIS", line: "Cameras registered against a map, with coverage and status at a glance." },
  { icon: ScanLine, title: "ANPR with multi-frame voting", line: "Plates read across several frames and reconciled for accuracy." },
  { icon: BellRing, title: "Watchlist alerts in real time", line: "Matches against watchlisted plates surface the moment they're seen." },
  { icon: Route, title: "Cross-camera re-identification & journeys", line: "Follow a vehicle's path as it moves between cameras." },
  { icon: Copy, title: "Cloned-plate detection", line: "Flags a plate seen in two places at once as impossible." },
  { icon: ShieldAlert, title: "Zones, intrusion & traffic baselines", line: "Learns normal traffic and flags zone intrusions and anomalies." },
  { icon: Sparkles, title: "Vision-language attributes & assistant", line: "Describe a vehicle in words; ask the assistant to find it." },
];

export function Capabilities() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-16">
      <h2 className="text-center text-2xl font-bold text-text sm:text-3xl">
        Capabilities
      </h2>
      <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {CAPABILITIES.map(({ icon: Icon, title, line }) => (
          <div
            key={title}
            className="rounded-card border border-border bg-surface p-5 transition-colors duration-200 hover:border-accent/60"
          >
            <Icon aria-hidden="true" className="h-6 w-6 text-accent" />
            <h3 className="mt-3 text-sm font-semibold text-text">{title}</h3>
            <p className="mt-1 text-sm text-muted">{line}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
