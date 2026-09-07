export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
const KEY = "NETRA_API_KEY", BASE = "NETRA_API_BASE";

/**
 * Tiny pub/sub so `api()` can announce a 401/403 from anywhere in the app
 * without importing lib/auth.tsx (which imports this module). AuthProvider
 * subscribes and turns these into a toast + a reopened sign-in dialog
 * (spec §4.6) so a stale/revoked key surfaces immediately, not just on the
 * sign-in form's own failed attempt.
 */
type AuthErrorListener = (status: 401 | 403) => void;
const authErrorListeners = new Set<AuthErrorListener>();
export function onAuthError(cb: AuthErrorListener): () => void {
  authErrorListeners.add(cb);
  return () => { authErrorListeners.delete(cb); };
}
function emitAuthError(status: 401 | 403) {
  authErrorListeners.forEach((cb) => cb(status));
}

export function apiBase(): string {
  if (typeof window !== "undefined") {
    const q = new URLSearchParams(window.location.search).get("api");
    if (q) { try { localStorage.setItem(BASE, q.replace(/\/$/, "")); } catch {} }
    try { const s = localStorage.getItem(BASE); if (s) return s; } catch {}
  }
  return (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8080").replace(/\/$/, "");
}
export function setApiBase(url: string) { try { localStorage.setItem(BASE, url.replace(/\/$/, "")); } catch {} }
export function apiUrl(path: string): string { return apiBase() + path; }
export function wsUrl(path: string): string { return apiBase().replace(/^http/, "ws") + path; }
export function getKey(): string | null { try { return localStorage.getItem(KEY); } catch { return null; } }
export function setKey(k: string | null) { try { if (k) localStorage.setItem(KEY, k); else localStorage.removeItem(KEY); } catch {} }

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const key = getKey(); if (key) headers.set("X-API-Key", key);
  let body = init.body;
  if (init.json !== undefined) { headers.set("Content-Type", "application/json"); body = JSON.stringify(init.json); }
  let res: Response;
  try { res = await fetch(apiUrl(path), { ...init, headers, body, cache: "no-store" }); }
  catch { throw new ApiError(0, "Backend unreachable"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail ?? j.error ?? msg; } catch {}
    // /api/auth/whoami's own 401s are handled locally by AuthProvider.refresh()
    // and SignInDialog's submit handler; emitting here would loop the dialog
    // back open the instant a viewer's anonymous whoami check runs.
    if ((res.status === 401 || res.status === 403) && !path.startsWith("/api/auth/whoami")) {
      emitAuthError(res.status);
    }
    throw new ApiError(res.status, String(msg));
  }
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("json") ? res.json() : res.text()) as Promise<T>;
}
