"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, getKey, setKey } from "./api";

export type Role = "viewer" | "operator" | "admin";
const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
type Who = { role: Role; name: string; enabled: boolean };
type Ctx = Who & { key: string | null; can: (min: Role) => boolean; signIn: (k: string) => Promise<Role>; signOut: () => void; refresh: () => void };
const AuthCtx = createContext<Ctx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [who, setWho] = useState<Who>({ role: "viewer", name: "anonymous", enabled: false });
  const [key, setK] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    setK(getKey());
    try { setWho(await api<Who>("/api/auth/whoami")); }
    catch (e) {
      // Backend older than Task A1 (no whoami) or unreachable: assume open mode as admin
      // so nothing is gated, matching the server's open-mode behaviour.
      if (e instanceof ApiError && e.status === 404) setWho({ role: "admin", name: "open mode", enabled: false });
      if (e instanceof ApiError && e.status === 401) { setKey(null); setK(null); setWho({ role: "viewer", name: "anonymous", enabled: true }); }
    }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const signIn = async (k: string) => {
    setKey(k.trim());
    try { const w = await api<Who>("/api/auth/whoami"); setWho(w); setK(k.trim()); return w.role; }
    catch (e) { setKey(null); throw e; }
  };
  const signOut = () => { setKey(null); refresh(); };
  const can = (min: Role) => RANK[who.role] >= RANK[min];
  return <AuthCtx.Provider value={{ ...who, key, can, signIn, signOut, refresh }}>{children}</AuthCtx.Provider>;
}
export function useAuth() { const c = useContext(AuthCtx); if (!c) throw new Error("AuthProvider missing"); return c; }
