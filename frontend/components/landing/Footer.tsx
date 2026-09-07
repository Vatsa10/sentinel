import Link from "next/link";

export function Footer() {
  return (
    <footer className="border-t border-border bg-surface">
      <div className="mx-auto flex max-w-6xl flex-col items-center gap-3 px-6 py-8 text-center text-sm text-muted sm:flex-row sm:justify-between sm:text-left">
        <p>
          Built for the Gujarat Police Innovation Challenge 2026 · Sentinel ·
          Vatsa Joshi
        </p>
        <nav aria-label="Footer" className="flex items-center gap-4">
          <Link href="https://github.com" className="hover:text-text" target="_blank" rel="noopener noreferrer">
            Repo
          </Link>
          <Link href="/docs/high-level-design.md" className="hover:text-text">
            HLD
          </Link>
          <a href="mailto:vatsajoshi2@gmail.com" className="hover:text-text">
            Contact
          </a>
        </nav>
      </div>
    </footer>
  );
}
