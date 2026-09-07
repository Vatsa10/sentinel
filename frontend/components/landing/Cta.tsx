import Link from "next/link";

export function Cta() {
  return (
    <section className="mx-auto max-w-4xl px-6 py-20 text-center">
      <h2 className="text-2xl font-bold text-text sm:text-3xl">
        See it running on your own feeds.
      </h2>
      <div className="mt-8 flex flex-wrap items-center justify-center gap-4">
        <Link
          href="/console"
          className="rounded-ctl bg-accent px-6 py-3 text-base font-semibold text-accent-fg transition-colors duration-200 hover:brightness-110"
        >
          Open Console
        </Link>
        <Link
          href="/console/wall"
          className="rounded-ctl border border-border bg-surface px-6 py-3 text-base font-semibold text-text transition-colors duration-200 hover:border-accent"
        >
          Watch the demo
        </Link>
      </div>
    </section>
  );
}
