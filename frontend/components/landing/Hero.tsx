import Link from "next/link";

export function Hero() {
  return (
    <section className="mx-auto max-w-6xl px-6 pb-16 pt-20 text-center sm:pt-28">
      <h1 className="animate-fade-up text-balance text-4xl font-extrabold tracking-tight text-text sm:text-6xl">
        See every camera. Trace every vehicle.
      </h1>
      <p className="animate-fade-up mx-auto mt-6 max-w-3xl text-balance text-lg text-muted sm:text-xl">
        NETRA unifies departmental CCTV into one console with ANPR, watchlist
        alerts and cross-camera vehicle intelligence. Built on Model 1
        (Registry &amp; GIS) + Model 2 (Unified Viewing &amp; Metadata
        Analytics).
      </p>
      <div className="animate-fade-up mt-9 flex flex-wrap items-center justify-center gap-4">
        <Link
          href="/console/"
          className="rounded-ctl bg-accent px-6 py-3 text-base font-semibold text-accent-fg transition-colors duration-200 hover:brightness-110"
        >
          Open Console
        </Link>
        <Link
          href="/console/wall/"
          className="rounded-ctl border border-border bg-surface px-6 py-3 text-base font-semibold text-text transition-colors duration-200 hover:border-accent"
        >
          Watch the demo
        </Link>
      </div>
    </section>
  );
}
