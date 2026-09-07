const STATS = [
  { value: "0%", label: "dropped frames on 8 cameras" },
  { value: "~13 ms", label: "per frame (FP16)" },
  { value: "0.95", label: "match score on own-feed plates" },
  { value: "15/30", label: "grid cameras anchor a clock" },
];

export function Measured() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-16">
      <h2 className="text-center text-2xl font-bold text-text sm:text-3xl">
        Measured, not promised
      </h2>
      <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {STATS.map((s) => (
          <div key={s.label} className="rounded-card border border-border bg-surface p-5 text-center">
            <div className="mono text-3xl font-semibold text-accent">{s.value}</div>
            <div className="mt-2 text-sm text-muted">{s.label}</div>
          </div>
        ))}
      </div>
      <p className="mx-auto mt-8 max-w-2xl text-center text-sm text-muted">
        Honesty check: across 2,691 sampled frames from the public traffic
        grid, zero plates were legible — grid cameras are for tracking motion
        and journeys, not for reading plates; plate reads come from
        dedicated ANPR feeds.
      </p>
    </section>
  );
}
