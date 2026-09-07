import { Camera, Cpu, Database, Radio, MonitorPlay, ArrowRight, ArrowDown } from "lucide-react";
import type { LucideIcon } from "lucide-react";

const NODES: { icon: LucideIcon; title: string }[] = [
  { icon: Camera, title: "Camera feed" },
  { icon: Cpu, title: "Edge inference" },
  { icon: Database, title: "Metadata store" },
  { icon: Radio, title: "Alerts & search" },
  { icon: MonitorPlay, title: "Console" },
];

export function HowItWorks() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-16">
      <h2 className="text-center text-2xl font-bold text-text sm:text-3xl">
        How it works
      </h2>
      <div className="mt-10 flex flex-col items-center gap-3 sm:flex-row sm:justify-center sm:gap-2">
        {NODES.map((node, i) => (
          <div key={node.title} className="flex flex-col items-center gap-3 sm:flex-row sm:gap-2">
            <div className="flex w-36 flex-col items-center gap-2 rounded-card border border-border bg-surface px-4 py-5 text-center">
              <node.icon aria-hidden="true" className="h-6 w-6 text-accent" />
              <span className="text-sm font-medium text-text">{node.title}</span>
            </div>
            {i < NODES.length - 1 && (
              <>
                <ArrowDown aria-hidden="true" className="h-5 w-5 text-muted sm:hidden" />
                <ArrowRight aria-hidden="true" className="hidden h-5 w-5 text-muted sm:block" />
              </>
            )}
          </div>
        ))}
      </div>
      <p className="mono mx-auto mt-10 max-w-xl rounded-card border border-accent/40 bg-surface px-5 py-4 text-center text-sm text-text">
        159&times; less bandwidth than streaming video to a centre — metadata
        travels, not video.
      </p>
    </section>
  );
}
