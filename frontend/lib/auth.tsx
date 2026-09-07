"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { toast } from "sonner";
import { api, ApiError, getKey, onAuthError, setKey } from "./api";

export type Role = "viewer" | "operator" | "admin";
const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
type Who = { role: Role; name: string; enabled: boolean };
type Ctx = Who & {
  key: string | null;
  can: (min: Role) => boolean;
  signIn: (k: string) => Promise<Role>;
  signOut: () => void;
  refresh: () => void;
  /** Controls SignInDialog from outside its own trigger button (spec §4.6):
   * a 401/403 from any gated request reopens it even when the trigger
   * isn't currently rendered (e.g. an operator hitting an admin-only route). */
  authDialogOpen: boolean;
  setAuthDialogOpen: (open: boolean) => void;
};
const AuthCtx = createContext<Ctx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [who, setWho] = useState<Who>({ role: "viewer", name: "anonymous", enabled: false });
  const [key, setK] = useState<string | null>(null);
  const [authDialogOpen, setAuthDialogOpen] = useState(false);
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
  useEffect(() => onAuthError((status) => {
    if (status === 401) {
      // The key on file is stale/revoked: drop it and fall back to viewer
      // so the badge reflects reality while the dialog is open.
      setKey(null); setK(null); setWho({ role: "viewer", name: "anonymous", enabled: true });
      toast.error("Session key rejected. Sign in again.");
    } else {
      toast.error("Your role may not do that. Sign in as operator or admin.");
    }
    setAuthDialogOpen(true);
  }), []);
  const signIn = async (k: string) => {
    setKey(k.trim());
    try { const w = await api<Who>("/api/auth/whoami"); setWho(w); setK(k.trim()); return w.role; }
    catch (e) { setKey(null); throw e; }
  };
  const signOut = () => { setKey(null); refresh(); };
  const can = (min: Role) => RANK[who.role] >= RANK[min];
  return (
    <AuthCtx.Provider value={{ ...who, key, can, signIn, signOut, refresh, authDialogOpen, setAuthDialogOpen }}>
      {children}
    </AuthCtx.Provider>
  );
}
export function useAuth() { const c = useContext(AuthCtx); if (!c) throw new Error("AuthProvider missing"); return c; }
