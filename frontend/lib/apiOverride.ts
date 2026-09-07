import { apiBase, setApiBase } from "@/lib/api";

/**
 * A `?api=` query param proposes retargeting the console to a different
 * backend. Because the stored `X-API-Key` is sent with every request, silently
 * accepting it (as `apiBase()` used to) would leak a sign-in key to whatever
 * URL is in the address bar. So the query value is never stored on its own —
 * it is only ever surfaced as a *pending* override for the user to confirm.
 */
export function pendingOverride(): string | null {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get("api");
  if (!raw) return null;
  const normalised = raw.replace(/\/$/, "");
  return normalised && normalised !== apiBase() ? normalised : null;
}

export function acceptOverride(url: string) {
  setApiBase(url);
  window.location.reload();
}

export function rejectOverride() {
  // Nothing was ever stored, so "reject" just means: drop the query param so
  // the dialog does not reappear on every navigation within this session.
  try {
    const u = new URL(window.location.href);
    u.searchParams.delete("api");
    window.history.replaceState(null, "", u.toString());
  } catch {}
}
