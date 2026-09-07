import Link from "next/link";
import { Brand } from "@/components/Brand";

export function Nav() {
  return (
    <header className="sticky top-0 z-50 border-b border-border bg-bg/85 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
        <Link href="/" aria-label="NETRA home">
          <Brand />
        </Link>
        <Link
          href="/console/"
          className="rounded-ctl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg transition-colors duration-200 hover:brightness-110"
        >
          Open Console
        </Link>
      </div>
    </header>
  );
}
